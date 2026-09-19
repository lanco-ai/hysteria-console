"""Durable, secret-safe state for the administrator video workflow."""

import math
import json
import uuid
import os
import tempfile
import time
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


def _classified_provider_error(exc: Exception) -> str | None:
    """Return the adapter's sanitized error code, if this is not retryable."""
    code = getattr(exc, 'code', None)
    return code if isinstance(code, str) and code and len(code) <= 80 else None


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


DEFAULT_RUNS_PATH = Path('/root/hysteria/state/video/runs.json')


class RunService:
    """Low-concurrency persisted DAG executor.

    ``tick`` is deliberately bounded and synchronous so a request or a safe
    existing lifecycle hook can drive it without introducing another worker
    service. A node is marked ``running`` before the paid request is made;
    ambiguous transport failures therefore cannot cause an automatic retry.
    """

    def __init__(self, workflows: WorkflowStore, settings: VideoSettings, provider, path: str | Path = DEFAULT_RUNS_PATH):
        self.workflows = workflows
        self.settings = settings
        self.provider = provider
        self.path = Path(path)

    def _records(self):
        value = state_store.load_json_strict(self.path, [])
        if not isinstance(value, list):
            raise VideoValidationError('invalid run storage')
        return value

    def _write(self, records):
        state_store.save_json(self.path, records)
        self.path.chmod(0o600)

    def _replace(self, run):
        records = self._records()
        records = [item for item in records if item.get('id') != run['id']]
        records.insert(0, run)
        lock_path = self.path.with_name(self.path.name + '.lock')
        with state_store.file_lock(lock_path):
            self._write(records)

    def get(self, run_id):
        return next((item for item in self._records() if item.get('id') == run_id), None)

    def list(self):
        return self._records()

    def submit(self, workflow_id, request_snapshot=None, shot_id=None):
        workflow = self.workflows.get(workflow_id)
        if workflow is None:
            raise VideoValidationError('workflow not found')
        workflow_snapshot = dict(workflow)
        if shot_id is not None:
            requested_shot = str(shot_id).strip()
            storyboard = workflow.get('storyboard') if isinstance(workflow.get('storyboard'), dict) else {}
            shots = storyboard.get('shots') if isinstance(storyboard.get('shots'), list) else []
            if not requested_shot or not any(
                isinstance(shot, dict) and str(shot.get('id')) == requested_shot for shot in shots
            ):
                raise VideoValidationError('storyboard shot not found')
            nodes = workflow.get('nodes', []) if isinstance(workflow.get('nodes'), list) else []
            selected_nodes = [
                node for node in nodes
                if isinstance(node, dict)
                and isinstance(node.get('data'), dict)
                and str(node['data'].get('shot_id')) == requested_shot
            ]
            selected_ids = {node.get('id') for node in selected_nodes}
            if not selected_nodes:
                raise VideoValidationError('storyboard shot has no executable nodes')
            workflow_snapshot['nodes'] = selected_nodes
            workflow_snapshot['edges'] = [
                edge for edge in workflow.get('edges', [])
                if isinstance(edge, dict)
                and edge.get('source') in selected_ids
                and edge.get('target') in selected_ids
            ]
        validated = validate_workflow(workflow_snapshot.get('nodes', []), workflow_snapshot.get('edges', []))
        node_status = {
            node['id']: {'state': 'queued'} for node in validated.nodes
        }
        run = {
            'id': uuid.uuid4().hex,
            'workflow_id': workflow_id,
            'state': 'queued',
            'created_at': int(time.time()),
            'order': validated.order,
            'workflow': {'nodes': validated.nodes, 'edges': validated.edges},
            'shot_id': str(shot_id) if shot_id is not None else None,
            'request': request_snapshot if isinstance(request_snapshot, dict) else {},
            'node_status': node_status,
            'provider_jobs': {},
            'assets': {},
        }
        if _contains_secret(run):
            raise VideoValidationError('run contains secret data')
        self._replace(run)
        return run

    @staticmethod
    def _node(run, node_id):
        return next(node for node in run['workflow']['nodes'] if node['id'] == node_id)

    @staticmethod
    def _source_value(run, node_id, target_port):
        for edge in run['workflow']['edges']:
            if edge.get('target') == node_id and (edge.get('targetHandle') or edge.get('target_port')) == target_port:
                source = edge.get('source')
                if source in run['assets']:
                    return run['assets'][source]
                source_node = next((node for node in run['workflow']['nodes'] if node['id'] == source), None)
                data = source_node.get('data') if isinstance(source_node, dict) else {}
                if isinstance(data, dict):
                    return data.get('text') or data.get('prompt') or data.get('asset_url')
                return None
        return None

    def _mark_ready_sources(self, run):
        for node_id in run['order']:
            node = self._node(run, node_id)
            status = run['node_status'][node_id]
            if status['state'] != 'queued':
                continue
            node_type = node['type']
            if node_type == 'prompt':
                status['state'] = 'succeeded'
            elif node_type == 'image_asset':
                value = (node.get('data') or {}).get('asset_url')
                if value:
                    status['state'] = 'succeeded'
                    run['assets'][node_id] = value
            elif node_type == 'preview':
                value = self._source_value(run, node_id, 'media')
                if value:
                    status['state'] = 'succeeded'
                    run['assets'][node_id] = value

    def tick(self, run_id):
        run = self.get(run_id)
        if run is None:
            raise VideoValidationError('run not found')
        if run['state'] in {'succeeded', 'failed', 'cancelled', 'cancel_unsupported'}:
            return run
        run['state'] = 'running'
        self._mark_ready_sources(run)
        for node_id in run['order']:
            node = self._node(run, node_id)
            status = run['node_status'][node_id]
            node_type = node['type']
            if status['state'] == 'running':
                job_id = run['provider_jobs'].get(node_id)
                if not job_id or status.get('submission_pending'):
                    continue
                try:
                    update = self.provider.get_job(job_id, self.settings)
                except Exception as exc:
                    code = _classified_provider_error(exc)
                    if code is not None:
                        status['state'] = 'failed'
                        run['error'] = code
                        run['state'] = 'failed'
                        break
                    continue
                if update.state == 'succeeded':
                    status['state'] = 'succeeded'
                    if update.asset_url:
                        run['assets'][node_id] = update.asset_url
                elif update.state == 'failed':
                    status['state'] = 'failed'
                    run['error'] = update.error_code or 'provider_failed'
                    run['state'] = 'failed'
                    break
                continue
            if status['state'] != 'queued':
                continue
            if node_type not in {'text_to_image', 'image_to_video', 'first_last_frame_video'}:
                continue
            data = node.get('data') if isinstance(node.get('data'), dict) else {}
            if node_type == 'text_to_image':
                prompt = self._source_value(run, node_id, 'prompt') or data.get('prompt')
                if not prompt:
                    status['state'] = 'failed'; run['error'] = 'missing_prompt'; run['state'] = 'failed'; break
                from .video_models import ImageRequest
                request = ImageRequest(str(prompt), str(data.get('model') or 'grok-imagine-image'))
                status['state'] = 'running'
                try:
                    job = self.provider.generate_image(request, self.settings)
                except Exception as exc:
                    code = _classified_provider_error(exc)
                    if code is not None:
                        status['state'] = 'failed'
                        run['error'] = code
                        run['state'] = 'failed'
                        break
                    # An unknown transport failure may happen after the
                    # provider accepted a paid request. Keep it pending so a
                    # later tick can reconcile the job instead of duplicating
                    # the submission.
                    status['submission_pending'] = True
                    continue
                run['provider_jobs'][node_id] = job.provider_job_id
                if job.asset_url:
                    run['assets'][node_id] = job.asset_url
                continue
            image_url = self._source_value(run, node_id, 'image')
            if node_type == 'first_last_frame_video':
                first = self._source_value(run, node_id, 'first_frame')
                last = self._source_value(run, node_id, 'last_frame')
                if not first or not last:
                    continue
                image_url = first
                from .video_models import VideoRequest
                request = VideoRequest(str(data.get('prompt') or ''), str(data.get('model') or 'grok-imagine-video'), first_frame_url=first, last_frame_url=last)
            else:
                if not image_url:
                    continue
                from .video_models import VideoRequest
                request = VideoRequest(str(data.get('prompt') or ''), str(data.get('model') or 'grok-imagine-video'), image_url=str(image_url))
            status['state'] = 'running'
            try:
                job = self.provider.generate_video(request, self.settings)
            except Exception as exc:
                code = _classified_provider_error(exc)
                if code is not None:
                    status['state'] = 'failed'
                    run['error'] = code
                    run['state'] = 'failed'
                    break
                status['submission_pending'] = True
                continue
            run['provider_jobs'][node_id] = job.provider_job_id
            if job.asset_url:
                run['assets'][node_id] = job.asset_url
        if run['state'] != 'failed' and all(item['state'] == 'succeeded' for item in run['node_status'].values()):
            run['state'] = 'succeeded'
        self._replace(run)
        return run

    def cancel(self, run_id):
        run = self.get(run_id)
        if run is None:
            raise VideoValidationError('run not found')
        job_id = next(iter(run['provider_jobs'].values()), None)
        if not job_id:
            run['state'] = 'cancel_unsupported'
            self._replace(run)
            return run
        result = self.provider.cancel_job(job_id, self.settings)
        run['state'] = 'cancelled' if result.status == 'cancelled' else 'cancel_unsupported'
        self._replace(run)
        return run

    def resume_pending(self):
        return [run for run in self._records() if run.get('state') in {'queued', 'running'}]
