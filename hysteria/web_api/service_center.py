"""Administrator service bookmarks. Monitoring is deliberately not connected yet."""

import copy
import hashlib
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import http_utils
import state_store
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .services import LoginRequired, StateUnavailable, UserAccessDenied


class Bookmark(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    description: str = Field(default='', max_length=500)
    category: str = Field(default='常用网站', max_length=40)
    api_base: str = Field(default='', max_length=2048)
    api_notes: str = Field(default='', max_length=2000)

    @field_validator('url', 'api_base')
    @classmethod
    def validate_url(cls, value, info):
        if not value and info.field_name == 'api_base':
            return value
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or '\\' in value or any(ord(char) < 32 for char in value)):
            raise ValueError('invalid URL')
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError('invalid port')
        return value


DEFAULT_ITEMS = [
    dict(id='codexproxy', name='CodexProxy · CPA', category='AI 接口',
         url='https://lancoai.site:9445/management.html#/login',
         description='CLIProxyAPI 多模型接口网关，管理授权账号、模型与客户端密钥。',
         api_base='https://lancoai.site:9445/v1',
         api_notes='GET /v1/models — 可用模型列表\nPOST /v1/chat/completions — OpenAI 兼容对话与流式回复\n调用需要 API Key；可用模型以模型列表为准。'),
    dict(id='grok2api', name='Grok2API', category='AI 接口',
         url='https://64.83.30.207:13004/request-audits',
         description='Grok API 网关与请求审计，查看调用记录、失败原因和账号情况。',
         api_base='https://64.83.30.207:13004/v1',
         api_notes='GET /v1/models — 可用模型列表\nPOST /v1/chat/completions — 对话与流式回复\nPOST /v1/responses — Responses 接口\nPOST /v1/images/generations — 图片生成\nPOST /v1/images/edits — 图片编辑\nPOST /v1/videos/generations — 创建视频任务\nGET /v1/videos/{id} — 查询视频任务\n调用需要 API Key；实际可用能力取决于账号与模型。'),
]


class ServiceCenterStore:
    def __init__(self, path=Path('/root/hysteria/state/service-center.json')):
        self.path = Path(path)

    @staticmethod
    def validate(items):
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError('invalid items')
        values = [Bookmark.model_validate(item).model_dump() for item in items]
        if len({item['id'] for item in values}) != len(values):
            raise ValueError('duplicate ids')
        return values

    def read(self):
        items = self.validate(state_store.load_json_strict(self.path, copy.deepcopy(DEFAULT_ITEMS)))
        revision = hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return dict(items=items, revision=revision)

    def replace(self, items, revision):
        values = self.validate(items)
        with state_store.file_lock(str(self.path) + '.lock', timeout=3):
            if revision != self.read()['revision']:
                raise ValueError('conflict')
            state_store.save_json(self.path, values)
            self.path.chmod(0o600)
            return self.read()


def register_service_center_routes(app, services, dispatch, store=None):
    store = store or ServiceCenterStore()

    async def guard(request):
        try:
            session = await dispatch(services.read_session, request)
        except LoginRequired:
            return JSONResponse({'error': 'login_required'}, status_code=401)
        except UserAccessDenied:
            return JSONResponse({'error': 'admin_required'}, status_code=403)
        except StateUnavailable:
            return JSONResponse({'error': 'state_unavailable'}, status_code=503)
        if isinstance(session, JSONResponse):
            return session
        if not isinstance(session, dict) or session.get('role') != 'admin':
            return JSONResponse({'error': 'admin_required'}, status_code=403)
        return None

    def read(*, headers, path):
        return store.read()

    def write(*, headers, path, values):
        return store.replace(values['items'], values['revision'])

    @app.get('/api/v1/admin/services')
    async def get_bookmarks(request: Request):
        denied = await guard(request)
        if denied is not None:
            return denied
        try:
            result = await dispatch(read, request)
            return result if isinstance(result, JSONResponse) else JSONResponse(result)
        except (state_store.StateStoreError, OSError, ValueError):
            return JSONResponse({'error': 'storage_unavailable'}, status_code=503)

    @app.put('/api/v1/admin/services')
    async def put_bookmarks(request: Request):
        denied = await guard(request)
        if denied is not None:
            return denied
        if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
            return JSONResponse({'error': 'cross_site_request'}, status_code=403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                return JSONResponse({'error': 'payload_too_large'}, status_code=413)
        try:
            values = json.loads(body)
            if not isinstance(values, dict) or set(values) != {'items', 'revision'} or not isinstance(values['revision'], str):
                raise ValueError('bad payload')
            result = await dispatch(partial(write, values=values), request)
            return result if isinstance(result, JSONResponse) else JSONResponse(result)
        except (state_store.StateStoreError, OSError):
            return JSONResponse({'error': 'storage_unavailable'}, status_code=503)
        except (ValueError, ValidationError) as exc:
            conflict = str(exc) == 'conflict'
            return JSONResponse({'error': 'revision_conflict' if conflict else 'invalid_bookmark'}, status_code=409 if conflict else 422)
