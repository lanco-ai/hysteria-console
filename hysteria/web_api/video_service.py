"""Durable, secret-safe state for the administrator video workflow."""

import math
import json
import uuid
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import state_store

from .video_models import ValidatedWorkflow, VideoSettings

DEFAULT_VIDEO_SETTINGS_PATH = Path('/root/hysteria/state/video/settings.json')
_MISSING = object()


class VideoSettingsError(ValueError):
    pass


class VideoValidationError(ValueError):
    pass


NODE_REGISTRY = {
    'prompt': {'inputs': set(), 'outputs': {'text'}},
    'image_asset': {'inputs': set(), 'outputs': {'image'}},
    'text_to_image': {'inputs': {'prompt'}, 'outputs': {'image'}},
    'image_to_video': {'inputs': {'image'}, 'outputs': {'video'}},
    'first_last_frame_video': {'inputs': {'first_frame', 'last_frame'}, 'outputs': {'video'}},
    'preview': {'inputs': {'media'}, 'outputs': set()},
}
DEFAULT_WORKFLOWS_PATH = Path('/root/hysteria/state/video/workflows.json')
DEFAULT_ASSETS_PATH = Path('/root/hysteria/state/video/assets')
ALLOWED_ASSET_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'video/mp4', 'video/webm'}


def mask_video_key(value: str) -> str:
    if not value:
        return ''
    if len(value) <= 8:
        return '•' * 8
    return f'{value[:3]}…{value[-4:]}'


def _validate(base_url: str, api_key: str, provider: str) -> None:
    if len(base_url) > 2048:
        raise VideoSettingsError('base_url is too long')
    parsed = urlsplit(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise VideoSettingsError('base_url must be an http(s) URL')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise VideoSettingsError('base_url contains unsupported components')
    if provider != 'grok':
        raise VideoSettingsError('unsupported provider')
    if not api_key or len(api_key) > 2048:
        raise VideoSettingsError('api_key is invalid')


def _coerce(raw: object) -> VideoSettings:
    if raw is None:
        return VideoSettings('', '')
    if not isinstance(raw, dict):
        raise VideoSettingsError('invalid settings shape')
    base_url = str(raw.get('base_url') or '').strip()
    api_key = str(raw.get('api_key') or '')
    provider = str(raw.get('provider') or 'grok').strip().lower()
    if not base_url or not api_key:
        return VideoSettings(base_url, api_key, provider)
    _validate(base_url, api_key, provider)
    return VideoSettings(base_url, api_key, provider)


class VideoSettingsStore:
    def __init__(self, path: str | Path = DEFAULT_VIDEO_SETTINGS_PATH):
        self.path = Path(path)

    def read(self) -> VideoSettings:
        try:
            raw = state_store.load_json_strict(self.path, {})
        except state_store.StateStoreError as exc:
            raise VideoSettingsError('settings unavailable') from exc
        return _coerce(raw)

    def public(self) -> dict[str, object]:
        settings = self.read()
        return {
            'provider': settings.provider,
            'base_url': settings.base_url,
            'api_key_configured': bool(settings.api_key),
            'api_key_masked': mask_video_key(settings.api_key),
        }

    def update(self, *, base_url: object = _MISSING, api_key: object = _MISSING, provider: object = _MISSING):
        lock_path = self.path.with_name(self.path.name + '.lock')
        try:
            with state_store.file_lock(lock_path):
                current = self.read()
                next_base = current.base_url if base_url is _MISSING else str(base_url or '').strip()
                next_key = current.api_key if api_key is _MISSING else str(api_key or '')
                next_provider = current.provider if provider is _MISSING else str(provider or '').strip().lower()
                _validate(next_base, next_key, next_provider)
                state_store.save_json(self.path, {
                    'provider': next_provider,
                    'base_url': next_base,
                    'api_key': next_key,
                })
                self.path.chmod(0o600)
        except (state_store.StateStoreError, OSError) as exc:
            raise VideoSettingsError('settings unavailable') from exc
        return self.public()


def _contains_secret(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower().replace('-', '_') in {'api_key', 'authorization', 'access_token', 'secret'}:
                return True
            if _contains_secret(item):
                return True
    elif isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


def validate_workflow(nodes, edges) -> ValidatedWorkflow:
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) > 100 or len(edges) > 300:
        raise VideoValidationError('invalid workflow shape')
    node_map = {}
    for raw in nodes:
        if not isinstance(raw, dict) or not isinstance(raw.get('id'), str) or not raw['id']:
            raise VideoValidationError('invalid node')
        node_id = raw['id']
        node_type = raw.get('type')
        if node_id in node_map or node_type not in NODE_REGISTRY:
            raise VideoValidationError('unknown node type')
        node_map[node_id] = raw
    adjacency = {node_id: [] for node_id in node_map}
    indegree = {node_id: 0 for node_id in node_map}
    normalized_edges = []
    for raw in edges:
        if not isinstance(raw, dict):
            raise VideoValidationError('invalid edge')
        source, target = raw.get('source'), raw.get('target')
        source_port = raw.get('sourceHandle') or raw.get('source_port')
        target_port = raw.get('targetHandle') or raw.get('target_port')
        if source not in node_map or target not in node_map:
            raise VideoValidationError('edge references unknown node')
        adjacency[source].append(target)
        indegree[target] += 1
        normalized_edges.append(raw)
    queue = [node_id for node_id in node_map if indegree[node_id] == 0]
    order = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for target in adjacency[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(order) != len(node_map):
        raise VideoValidationError('workflow contains cycle')
    for raw in normalized_edges:
        source, target = raw.get('source'), raw.get('target')
        source_port = raw.get('sourceHandle') or raw.get('source_port')
        target_port = raw.get('targetHandle') or raw.get('target_port')
        if source_port not in NODE_REGISTRY[node_map[source]['type']]['outputs']:
            raise VideoValidationError('invalid source port')
        if target_port not in NODE_REGISTRY[node_map[target]['type']]['inputs']:
            raise VideoValidationError('invalid target port')
    connected = {(edge.get('target'), edge.get('targetHandle') or edge.get('target_port')) for edge in normalized_edges}
    for node_id, raw in node_map.items():
        required = NODE_REGISTRY[raw['type']]['inputs']
        data = raw.get('data') if isinstance(raw.get('data'), dict) else {}
        for port in required:
            if (node_id, port) not in connected and not data.get(port):
                raise VideoValidationError(f'missing required input: {port}')
    return ValidatedWorkflow(list(node_map.values()), normalized_edges, order)


class WorkflowStore:
    def __init__(self, path: str | Path = DEFAULT_WORKFLOWS_PATH):
        self.path = Path(path)

    def _read_all(self):
        try:
            value = state_store.load_json_strict(self.path, [])
        except state_store.StateStoreError as exc:
            raise VideoValidationError('workflow storage unavailable') from exc
        if not isinstance(value, list):
            raise VideoValidationError('invalid workflow storage')
        return value

    def list(self):
        return self._read_all()

    def get(self, workflow_id):
        return next((item for item in self._read_all() if item.get('id') == workflow_id), None)

    def save(self, workflow):
        if not isinstance(workflow, dict) or _contains_secret(workflow):
            raise VideoValidationError('workflow contains secret data')
        validated = validate_workflow(workflow.get('nodes', []), workflow.get('edges', []))
        candidate = dict(workflow)
        candidate['id'] = str(workflow.get('id') or uuid.uuid4().hex)
        candidate['version'] = int(workflow.get('version') or 1)
        candidate['nodes'] = validated.nodes
        candidate['edges'] = validated.edges
        if len(json.dumps(candidate, ensure_ascii=False)) > 512 * 1024:
            raise VideoValidationError('workflow is too large')
        lock_path = self.path.with_name(self.path.name + '.lock')
        with state_store.file_lock(lock_path):
            records = self._read_all()
            records = [item for item in records if item.get('id') != candidate['id']]
            records.insert(0, candidate)
            state_store.save_json(self.path, records)
            self.path.chmod(0o600)
        return candidate

    def delete(self, workflow_id):
        lock_path = self.path.with_name(self.path.name + '.lock')
        with state_store.file_lock(lock_path):
            records = self._read_all()
            next_records = [item for item in records if item.get('id') != workflow_id]
            changed = len(next_records) != len(records)
            if changed:
                state_store.save_json(self.path, next_records)
                self.path.chmod(0o600)
            return changed


class AssetStore:
    def __init__(self, root: str | Path = DEFAULT_ASSETS_PATH, *, max_bytes: int = 20 * 1024 * 1024):
        self.root = Path(root)
        self.max_bytes = int(max_bytes)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.metadata_path = self.root / 'index.json'

    def _metadata(self):
        value = state_store.load_json_strict(self.metadata_path, [])
        if not isinstance(value, list):
            raise VideoValidationError('invalid asset storage')
        return value

    def save_upload(self, filename: str, content_type: str, body: bytes):
        name = Path(str(filename or '')).name
        if not name or name != str(filename) or '..' in name or content_type not in ALLOWED_ASSET_TYPES:
            raise VideoValidationError('unsupported asset')
        if not isinstance(body, bytes) or len(body) > self.max_bytes:
            raise VideoValidationError('asset is too large')
        asset_id = uuid.uuid4().hex
        suffix = Path(name).suffix.lower()
        target = self.root / f'{asset_id}{suffix}'
        fd, temp_name = tempfile.mkstemp(prefix=asset_id + '.', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        metadata = {'id': asset_id, 'filename': name, 'content_type': content_type, 'size': len(body)}
        lock_path = self.metadata_path.with_name(self.metadata_path.name + '.lock')
        with state_store.file_lock(lock_path):
            records = self._metadata()
            records.insert(0, metadata)
            state_store.save_json(self.metadata_path, records)
            self.metadata_path.chmod(0o600)
        return metadata

    def get(self, asset_id: str):
        if not isinstance(asset_id, str) or not asset_id.isalnum():
            return None
        return next((item for item in self._metadata() if item.get('id') == asset_id), None)

    def open(self, asset_id: str):
        metadata = self.get(asset_id)
        if metadata is None:
            return None, None
        matches = list(self.root.glob(f'{asset_id}.*'))
        if len(matches) != 1:
            return None, None
        return metadata, matches[0]
