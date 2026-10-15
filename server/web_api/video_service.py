"""Durable, secret-safe state for the administrator video workflow."""

import math
import copy
import json
import base64
import ipaddress
import uuid
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import state_store

from .video_models import ValidatedWorkflow, VideoSettings

DEFAULT_VIDEO_SETTINGS_PATH = Path('/root/hysteria/state/video/settings.json')
_MISSING = object()


class VideoSettingsError(ValueError):
    pass


class VideoValidationError(ValueError):
    pass


class VideoStorageFullError(VideoValidationError):
    pass


def _classified_provider_error(exc: Exception) -> str | None:
    """Return the adapter's sanitized error code, if this is not retryable."""
    code = getattr(exc, 'code', None)
    return code if isinstance(code, str) and code and len(code) <= 80 else None


def _submission_rejection(exc: Exception) -> str | None:
    code = _classified_provider_error(exc)
    status = getattr(exc, 'status', None)
    if (isinstance(status, int) and 400 <= status < 500 and status != 408
            or code in {'invalid_image_count', 'image_batch_unsupported', 'first_last_frame_unsupported', 'invalid_base_url'}):
        return code
    return None


NODE_REGISTRY = {
    'prompt': {'inputs': set(), 'outputs': {'text'}},
    'image_asset': {'inputs': set(), 'outputs': {'image'}},
    'text_to_image': {'inputs': {'prompt'}, 'outputs': {'image', 'first_frame', 'last_frame'}},
    'image_to_video': {'inputs': {'image'}, 'outputs': {'video'}},
    'first_last_frame_video': {'inputs': {'first_frame', 'last_frame'}, 'outputs': {'video'}},
    'preview': {'inputs': {'media'}, 'outputs': set()},
}
DEFAULT_WORKFLOWS_PATH = Path('/root/hysteria/state/video/workflows.json')
DEFAULT_ASSETS_PATH = Path('/root/hysteria/state/video/assets')
ALLOWED_ASSET_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'video/mp4', 'video/webm'}
MAX_PROVIDER_IMAGE_BYTES = 8 * 1024 * 1024
MAX_RUN_DURATION_SECONDS = 60 * 60
MAX_ASSET_STORAGE_BYTES = 256 * 1024 * 1024


def _ensure_private_directory(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


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
    try:
        parsed.port
    except ValueError as exc:
        raise VideoSettingsError('base_url has an invalid port') from exc
    if parsed.scheme == 'http':
        hostname = parsed.hostname
        try:
            is_loopback = ipaddress.ip_address(hostname or '').is_loopback
        except ValueError:
            is_loopback = (hostname or '').lower().rstrip('.') == 'localhost'
        if not is_loopback:
            raise VideoSettingsError('base_url must use HTTPS or loopback HTTP')
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
        _ensure_private_directory(self.path.parent)

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


def validate_workflow(nodes, edges, *, require_all_inputs=True, cached_image_nodes=frozenset()) -> ValidatedWorkflow:
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
    if require_all_inputs:
        connected = {(edge.get('target'), edge.get('targetHandle') or edge.get('target_port')) for edge in normalized_edges}
        for node_id, raw in node_map.items():
            required = NODE_REGISTRY[raw['type']]['inputs']
            data = raw.get('data') if isinstance(raw.get('data'), dict) else {}
            for port in required:
                if raw['type'] == 'text_to_image' and port == 'prompt' and node_id in cached_image_nodes:
                    continue
                if (node_id, port) not in connected and not data.get(port):
                    raise VideoValidationError(f'missing required input: {port}')
    return ValidatedWorkflow(list(node_map.values()), normalized_edges, order)


class WorkflowStore:
    def __init__(self, path: str | Path = DEFAULT_WORKFLOWS_PATH):
        self.path = Path(path)
        _ensure_private_directory(self.path.parent)

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

    def prepare_run(self, workflow_id):
        """Assign missing video incarnations in legacy drafts under their lock."""
        with state_store.file_lock(self.path.with_name(self.path.name + '.lock')):
            records = self._read_all()
            workflow = next((item for item in records if item.get('id') == workflow_id), None)
            changed = False
            for node in (workflow or {}).get('nodes', []):
                if node.get('type') in {'image_to_video', 'first_last_frame_video'}:
                    data = node.setdefault('data', {})
                    if not data.get('video_node_token'):
                        data['video_node_token'] = uuid.uuid4().hex
                        changed = True
            if changed:
                state_store.save_json(self.path, records)
                self.path.chmod(0o600)
            return workflow

    def save(self, workflow):
        if not isinstance(workflow, dict) or _contains_secret(workflow):
            raise VideoValidationError('workflow contains secret data')
        # Canvas workflows are editable drafts; only run snapshots require complete inputs.
        validated = validate_workflow(workflow.get('nodes', []), workflow.get('edges', []), require_all_inputs=False)
        candidate = copy.deepcopy(workflow)
        candidate['id'] = str(workflow.get('id') or uuid.uuid4().hex)
        candidate['version'] = int(workflow.get('version') or 1)
        candidate['nodes'] = copy.deepcopy(validated.nodes)
        candidate['edges'] = validated.edges
        if len(json.dumps(candidate, ensure_ascii=False)) > 512 * 1024:
            raise VideoValidationError('workflow is too large')
        lock_path = self.path.with_name(self.path.name + '.lock')
        with state_store.file_lock(lock_path):
            records = self._read_all()
            previous = next((item for item in records if item.get('id') == candidate['id']), None)
            previous_nodes = {node['id']: node for node in (previous or {}).get('nodes', [])}
            for node in candidate['nodes']:
                old = previous_nodes.get(node['id'], {})
                if node.get('type') in {'image_to_video', 'first_last_frame_video'}:
                    data = node.get('data') if isinstance(node.get('data'), dict) else {}
                    node['data'] = data
                    current = old.get('data', {}) if old.get('type') == node['type'] else {}
                    data['video_node_token'] = current.get('video_node_token') or uuid.uuid4().hex
                    # Only the server owns result and run metadata. Stale drafts
                    # may edit the graph without erasing a completed result.
                    for key in ('video_run_id', 'video_run_state', 'video_run_version', 'video_url'):
                        if key in current:
                            data[key] = current[key]
                        else:
                            data.pop(key, None)
                    continue
                if node.get('type') != 'text_to_image':
                    continue
                data = node.get('data') if isinstance(node.get('data'), dict) else {}
                node['data'] = data
                current = old.get('data') if old.get('type') == 'text_to_image' and isinstance(old.get('data'), dict) else {}
                data['candidate_node_token'] = current.get('candidate_node_token') if old.get('type') == 'text_to_image' else uuid.uuid4().hex
                # Run ownership is server controlled. A draft from before completion
                # may update the graph, but cannot erase a newer batch or its picks.
                for key in ('candidate_run_id', 'candidate_run_state', 'candidate_run_version'):
                    if key in current:
                        data[key] = current[key]
                    else:
                        data.pop(key, None)
                if current.get('candidate_batch_id'):
                    keys = ['candidate_batch_id', 'candidate_batch_version', 'candidate_asset_refs']
                    selection_keys = ['selected_first_asset_ref', 'selected_last_asset_ref']
                    selection_version = int(current.get('candidate_selection_version') or 0)
                    if (data.get('candidate_batch_id') != current['candidate_batch_id']
                            or data.get('candidate_selection_version', 0) != selection_version):
                        keys += selection_keys
                    elif any(data.get(key, '') != current.get(key, '') for key in selection_keys):
                        selection_version += 1
                    data['candidate_selection_version'] = selection_version
                    for key in keys:
                        data[key] = copy.deepcopy(current.get(key, ''))
                else:
                    data.pop('candidate_batch_id', None)
                    data.pop('candidate_batch_version', None)
                    data.pop('candidate_selection_version', None)
            records = [item for item in records if item.get('id') != candidate['id']]
            records.insert(0, candidate)
            state_store.save_json(self.path, records)
            self.path.chmod(0o600)
        return candidate

    def reconcile_candidate_run(self, run, *, claim=False):
        """Merge only the current image target's server fields under the draft lock."""
        target = run.get('target_node_id')
        snapshot = next((node for node in run.get('workflow', {}).get('nodes', [])
                         if node.get('id') == target and node.get('type') == 'text_to_image'), None)
        if not target or snapshot is None:
            return
        with state_store.file_lock(self.path.with_name(self.path.name + '.lock')):
            records = self._read_all()
            workflow = next((item for item in records if item.get('id') == run['workflow_id']), None)
            node = next((item for item in (workflow or {}).get('nodes', [])
                         if item.get('id') == target and item.get('type') == 'text_to_image'), None)
            if node is None:
                return
            data = node.setdefault('data', {})
            if data.get('candidate_node_token') != snapshot.get('data', {}).get('candidate_node_token'):
                return
            if claim:
                if int(run.get('submission_sequence') or 0) <= int(data.get('candidate_run_version') or 0):
                    return
            elif data.get('candidate_run_id') != run['id']:
                return
            before = copy.deepcopy(data)
            if claim:
                data['candidate_run_version'] = run['submission_sequence']
            data['candidate_run_id'] = run['id']
            data['candidate_run_state'] = run['state']
            refs = run.get('assets', {}).get(target)
            if (run['state'] == 'succeeded' and data.get('candidate_batch_id') != run['id']
                    and isinstance(refs, list) and refs
                    and all(isinstance(ref, str) and ref.startswith('asset://') for ref in refs)):
                data.update(candidate_batch_id=run['id'], candidate_batch_version=data.get('candidate_run_version', 1), candidate_asset_refs=refs,
                            candidate_selection_version=0, selected_first_asset_ref='', selected_last_asset_ref='')
            if data != before:
                state_store.save_json(self.path, records)
                self.path.chmod(0o600)

    def reconcile_video_run(self, run, *, claim=False):
        """Keep the result on its video node; previews resolve their current edge."""
        target = run.get('target_node_id')
        snapshot = next((node for node in run.get('workflow', {}).get('nodes', [])
                         if node.get('id') == target and node.get('type') in {'image_to_video', 'first_last_frame_video'}), None)
        if snapshot is None:
            return
        with state_store.file_lock(self.path.with_name(self.path.name + '.lock')):
            records = self._read_all()
            workflow = next((item for item in records if item.get('id') == run['workflow_id']), None)
            node = next((item for item in (workflow or {}).get('nodes', [])
                         if item.get('id') == target and item.get('type') == snapshot['type']), None)
            if node is None:
                return
            data = node.setdefault('data', {})
            token = snapshot.get('data', {}).get('video_node_token')
            if not token or data.get('video_node_token') != token:
                return
            if claim:
                if int(run.get('submission_sequence') or 0) <= int(data.get('video_run_version') or 0):
                    return
            elif data.get('video_run_id') != run['id']:
                return
            before = copy.deepcopy(data)
            if claim:
                data['video_run_version'] = run['submission_sequence']
            data['video_run_id'] = run['id']
            data['video_run_state'] = run['state']
            url = run.get('assets', {}).get(target)
            if run['state'] == 'succeeded' and isinstance(url, str) and url:
                data['video_url'] = f'/api/video/runs/{quote(run["id"], safe="")}/assets/{quote(target, safe="")}/content'
            if data != before:
                state_store.save_json(self.path, records)
                self.path.chmod(0o600)

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
    def __init__(
        self,
        root: str | Path = DEFAULT_ASSETS_PATH,
        *,
        max_bytes: int = 20 * 1024 * 1024,
        max_total_bytes: int = MAX_ASSET_STORAGE_BYTES,
    ):
        self.root = Path(root)
        self.max_bytes = int(max_bytes)
        self.max_total_bytes = int(max_total_bytes)
        if self.max_bytes <= 0 or self.max_total_bytes <= 0:
            raise ValueError('asset storage limits must be positive')
        _ensure_private_directory(self.root.parent)
        _ensure_private_directory(self.root)
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
        metadata = {'id': asset_id, 'filename': name, 'content_type': content_type, 'size': len(body)}
        lock_path = self.metadata_path.with_name(self.metadata_path.name + '.lock')
        with state_store.file_lock(lock_path):
            records = self._metadata()
            used_bytes = 0
            for record in records:
                if not isinstance(record, dict):
                    raise VideoValidationError('invalid asset storage')
                size = record.get('size')
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    raise VideoValidationError('invalid asset storage')
                used_bytes += size
            if used_bytes + len(body) > self.max_total_bytes:
                raise VideoStorageFullError('asset storage is full')
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
            records.insert(0, metadata)
            try:
                state_store.save_json(self.metadata_path, records)
            except Exception:
                target.unlink(missing_ok=True)
                raise
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

    def data_url(self, asset_id: str) -> str:
        metadata, path = self.open(asset_id)
        if metadata is None or path is None or not str(metadata.get('content_type', '')).startswith('image/'):
            raise VideoValidationError('asset unavailable')
        size = int(metadata.get('size') or 0)
        if size <= 0 or size > MAX_PROVIDER_IMAGE_BYTES:
            raise VideoValidationError('asset is too large for provider')
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise VideoValidationError('asset unavailable') from exc
        if len(body) != size or len(body) > MAX_PROVIDER_IMAGE_BYTES:
            raise VideoValidationError('asset is too large for provider')
        encoded = base64.b64encode(body).decode('ascii')
        return f"data:{metadata['content_type']};base64,{encoded}"


DEFAULT_RUNS_PATH = Path('/root/hysteria/state/video/runs.json')


class RunService:
    """Low-concurrency persisted DAG executor.

    ``tick`` is deliberately bounded and synchronous so a request or a safe
    existing lifecycle hook can drive it without introducing another worker
    service. A node is marked ``running`` before the paid request is made;
    ambiguous transport failures therefore cannot cause an automatic retry.
    """

    def __init__(self, workflows: WorkflowStore, settings: VideoSettings, provider, path: str | Path = DEFAULT_RUNS_PATH, asset_store: AssetStore | None = None):
        self.workflows = workflows
        self.settings = settings
        self.provider = provider
        self.path = Path(path)
        _ensure_private_directory(self.path.parent)
        self.asset_store = asset_store

    def _records(self):
        value = state_store.load_json_strict(self.path, [])
        if not isinstance(value, list):
            raise VideoValidationError('invalid run storage')
        return value

    def _write(self, records):
        state_store.save_json(self.path, records)
        self.path.chmod(0o600)

    def _replace(self, run, *, new_submission=False, reconcile=True):
        lock_path = self.path.with_name(self.path.name + '.lock')
        with state_store.file_lock(lock_path):
            records = self._records()
            previous = next((item for item in records if item.get('id') == run['id']), None)
            if not new_submission and previous is not None and previous.get('storage_version', 0) != run.get('storage_version', 0):
                return False
            run['storage_version'] = int(run.get('storage_version') or 0) + 1
            if new_submission:
                run['submission_sequence'] = max((int(item.get('submission_sequence') or 0) for item in records), default=0) + 1
            records = [item for item in records if item.get('id') != run['id']]
            records.insert(0, run)
            self._write(records)
        if reconcile:
            self.workflows.reconcile_candidate_run(run)
            self.workflows.reconcile_video_run(run)
        return True

    def get(self, run_id):
        return next((item for item in self._records() if item.get('id') == run_id), None)

    def list(self):
        return self._records()

    def submit(self, workflow_id, request_snapshot=None, shot_id=None, target_node_id=None):
        workflow = self.workflows.prepare_run(workflow_id)
        if workflow is None:
            raise VideoValidationError('workflow not found')
        workflow_snapshot = dict(workflow)
        if target_node_id is not None:
            if not isinstance(target_node_id, str) or not target_node_id:
                raise VideoValidationError('invalid target node')
            nodes = workflow.get('nodes', [])
            node_ids = {node.get('id') for node in nodes if isinstance(node, dict)}
            if target_node_id not in node_ids:
                raise VideoValidationError('target node not found')
            required = {target_node_id}
            edges = workflow.get('edges', [])
            while True:
                next_required = required | {
                    edge.get('source') for edge in edges
                    if isinstance(edge, dict) and edge.get('target') in required
                }
                if next_required == required:
                    break
                required = next_required
            workflow_snapshot['nodes'] = [node for node in nodes if node.get('id') in required]
            workflow_snapshot['edges'] = [edge for edge in edges if edge.get('source') in required and edge.get('target') in required]
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
        cached_image_nodes = set()
        if target_node_id is not None:
            for node in workflow_snapshot.get('nodes', []):
                if not isinstance(node, dict) or node.get('type') != 'text_to_image' or node.get('id') == target_node_id:
                    continue
                data = node.get('data') if isinstance(node.get('data'), dict) else {}
                refs = data.get('candidate_asset_refs')
                if isinstance(refs, list) and refs and all(isinstance(ref, str) and ref.startswith('asset://') for ref in refs):
                    cached_image_nodes.add(node['id'])
        validated = validate_workflow(workflow_snapshot.get('nodes', []), workflow_snapshot.get('edges', []),
                                      cached_image_nodes=cached_image_nodes)
        self._validate_connected_prompts(validated, cached_image_nodes)
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
            'target_node_id': target_node_id,
            'request': request_snapshot if isinstance(request_snapshot, dict) else {},
            'node_status': node_status,
            'provider_jobs': {},
            'assets': {},
        }
        if _contains_secret(run):
            raise VideoValidationError('run contains secret data')
        # Serialize receipt creation with the irreversible submission claim.
        # Neither this gate nor the draft lock is held during provider I/O.
        with state_store.file_lock(self.path.with_name(self.path.name + '.submission.lock')):
            for existing in self._records():
                if (self._same_target(existing, run) and existing.get('state') == 'running'):
                    return existing
            self._replace(run, new_submission=True, reconcile=False)
            try:
                self.workflows.reconcile_candidate_run(run, claim=True)
                self.workflows.reconcile_video_run(run, claim=True)
            except Exception:
                # The durable queue already accepted this receipt. The worker
                # repairs missing draft metadata before its conditional claim.
                pass
        return run

    @staticmethod
    def _same_target(left, right):
        if left.get('workflow_id') != right.get('workflow_id') or left.get('target_node_id') != right.get('target_node_id'):
            return False
        target = right.get('target_node_id')
        if target is None:
            return left.get('shot_id') == right.get('shot_id')
        def identity(run):
            node = next((node for node in run['workflow']['nodes'] if node['id'] == target), {})
            data = node.get('data', {})
            return node.get('type'), data.get('candidate_node_token'), data.get('video_node_token')
        return identity(left) == identity(right)

    def _claim_submission(self, run, node_id):
        """Atomically check ownership and persist the only permission to POST."""
        with state_store.file_lock(self.path.with_name(self.path.name + '.submission.lock')):
            self.workflows.reconcile_candidate_run(run, claim=True)
            self.workflows.reconcile_video_run(run, claim=True)
            with state_store.file_lock(self.workflows.path.with_name(self.workflows.path.name + '.lock')):
                records = self._records()
                current = next((item for item in records if item['id'] == run['id']), None)
                if (current is None or current.get('storage_version', 0) != run.get('storage_version', 0)
                        or current['node_status'][node_id]['state'] != 'queued'
                        or current['node_status'][node_id].get('submission_pending')
                        or current['state'] not in {'queued', 'running'}):
                    return False
                target = run.get('target_node_id')
                snapshot = next((node for node in run['workflow']['nodes'] if node['id'] == target), None)
                if snapshot and snapshot['type'] in {'text_to_image', 'image_to_video', 'first_last_frame_video'}:
                    workflow = self.workflows.get(run['workflow_id'])
                    node = next((node for node in (workflow or {}).get('nodes', [])
                                 if node['id'] == target and node['type'] == snapshot['type']), None)
                    prefix = 'candidate' if snapshot['type'] == 'text_to_image' else 'video'
                    token = f'{prefix}_node_token'
                    newer = any(self._same_target(item, run) and item.get('submission_sequence', 0) > run.get('submission_sequence', 0)
                                for item in records)
                    if node is None or node.get('data', {}).get(token) != snapshot.get('data', {}).get(token) or newer:
                        run['state'] = 'cancelled'
                        run['error'] = 'superseded_before_submission'
                        for status in run['node_status'].values():
                            status['state'] = 'cancelled'
                        self._replace(run, reconcile=False)
                        return False
                run['node_status'][node_id].update(state='running', submission_pending=True)
                return self._replace(run, reconcile=False)

    def _preflight(self, kind, request):
        prepare = getattr(self.provider, f'prepare_{kind}', None)
        if prepare is not None:
            # Preparation performs capability discovery and validation only.
            # The returned callable begins at the potentially paid POST.
            return prepare(request, self.settings)
        return lambda: getattr(self.provider, f'generate_{kind}')(request, self.settings)

    @staticmethod
    def _validate_connected_prompts(validated, cached_image_nodes=frozenset()):
        """Reject connected prompt ports whose source has no usable text."""
        nodes = {node['id']: node for node in validated.nodes}
        for node in validated.nodes:
            if node.get('type') != 'text_to_image' or node['id'] in cached_image_nodes:
                continue
            data = node.get('data') if isinstance(node.get('data'), dict) else {}
            edge = next((item for item in validated.edges
                         if item.get('target') == node['id']
                         and (item.get('targetHandle') or item.get('target_port')) == 'prompt'), None)
            if edge is None:
                prompt = data.get('prompt')
            else:
                source = nodes.get(edge.get('source'))
                source_data = source.get('data') if isinstance(source, dict) and isinstance(source.get('data'), dict) else {}
                prompt = source_data.get('text')
            if not prompt or isinstance(prompt, str) and not prompt.strip():
                raise VideoValidationError('missing required input: prompt')

    @staticmethod
    def _node(run, node_id):
        return next(node for node in run['workflow']['nodes'] if node['id'] == node_id)

    @staticmethod
    def _source_value(run, node_id, target_port):
        for edge in run['workflow']['edges']:
            if edge.get('target') == node_id and (edge.get('targetHandle') or edge.get('target_port')) == target_port:
                source = edge.get('source')
                source_handle = edge.get('sourceHandle') or edge.get('source_port')
                source_node = next((node for node in run['workflow']['nodes'] if node['id'] == source), None)
                data = source_node.get('data') if isinstance(source_node, dict) else {}
                if source_node and source_node.get('type') == 'text_to_image' and source_handle in {'first_frame', 'last_frame'}:
                    key = 'selected_first_asset_ref' if source_handle == 'first_frame' else 'selected_last_asset_ref'
                    selected = data.get(key) if isinstance(data, dict) else None
                    candidates = run['assets'].get(source)
                    if not candidates and isinstance(data, dict):
                        candidates = data.get('candidate_asset_refs')
                    return selected if isinstance(candidates, list) and selected in candidates else None
                if source in run['assets']:
                    value = run['assets'][source]
                    return value[0] if isinstance(value, list) and value else value
                if source_handle == 'text':
                    return data.get('text') if isinstance(data, dict) else None
                if isinstance(data, dict):
                    return data.get('text') or data.get('prompt') or data.get('asset_url')
                return None
        return None

    def _provider_image(self, value):
        if isinstance(value, str) and value.startswith('asset://'):
            if self.asset_store is None:
                raise VideoValidationError('asset unavailable')
            return self.asset_store.data_url(value[len('asset://'):])
        return value

    def _archive_images(self, urls):
        if self.asset_store is None:
            raise VideoValidationError('asset unavailable')
        refs = []
        for index, url in enumerate(urls):
            media = self.provider.open_asset(url, self.settings)
            try:
                if not media.content_type.startswith('image/'):
                    raise VideoValidationError('invalid image media')
                body = bytearray()
                for chunk in media.iter_bytes():
                    if len(body) + len(chunk) > min(self.asset_store.max_bytes, MAX_PROVIDER_IMAGE_BYTES):
                        raise VideoValidationError('asset is too large')
                    body.extend(chunk)
                suffix = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'}.get(media.content_type)
                if suffix is None or not body:
                    raise VideoValidationError('invalid image media')
                asset = self.asset_store.save_upload(f'candidate-{index + 1}.{suffix}', media.content_type, bytes(body))
                refs.append(f"asset://{asset['id']}")
            finally:
                media.close()
        return refs

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
                data = node.get('data') if isinstance(node.get('data'), dict) else {}
                value = data.get('asset_ref') or data.get('asset_url')
                if value:
                    status['state'] = 'succeeded'
                    run['assets'][node_id] = value
                else:
                    status['state'] = 'failed'
                    run['error'] = 'missing_asset'
                    run['state'] = 'failed'
            elif node_type == 'text_to_image' and run.get('target_node_id') != node_id:
                data = node.get('data') if isinstance(node.get('data'), dict) else {}
                refs = data.get('candidate_asset_refs')
                if isinstance(refs, list) and refs and all(isinstance(ref, str) and ref.startswith('asset://') for ref in refs):
                    status['state'] = 'succeeded'
                    run['assets'][node_id] = refs
                elif run.get('target_node_id') is not None:
                    status['state'] = 'failed'
                    run['error'] = 'missing_candidates'
                    run['state'] = 'failed'
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
            self.workflows.reconcile_candidate_run(run)
            self.workflows.reconcile_video_run(run)
            return run
        if any(status.get('submission_pending') for status in run.get('node_status', {}).values()):
            # A deadline or cancellation cannot establish whether the provider
            # accepted this request. Keep ownership until explicitly resolved.
            run['error'] = 'submission_unknown'
            return run
        created_at = run.get('created_at')
        if isinstance(created_at, (int, float)) and time.time() - created_at > MAX_RUN_DURATION_SECONDS:
            for status in run.get('node_status', {}).values():
                if isinstance(status, dict) and status.get('state') in {'queued', 'running'}:
                    status['state'] = 'failed'
            run['state'] = 'failed'
            run['error'] = 'run_timeout'
            self._replace(run)
            return run
        run['state'] = 'running'
        self._mark_ready_sources(run)
        if run['state'] == 'failed':
            self._replace(run)
            return run
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
                        try:
                            run['assets'][node_id] = self._archive_images([update.asset_url]) if node_type == 'text_to_image' and self.asset_store else update.asset_url
                        except Exception as exc:
                            status['state'] = 'failed'
                            run['error'] = 'asset_storage_full' if isinstance(exc, VideoStorageFullError) else _classified_provider_error(exc) or 'asset_archive_failed'
                            run['state'] = 'failed'
                            break
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
                connected_prompt = any(
                    edge.get('target') == node_id
                    and (edge.get('targetHandle') or edge.get('target_port')) == 'prompt'
                    for edge in run['workflow']['edges']
                )
                prompt = self._source_value(run, node_id, 'prompt') if connected_prompt else data.get('prompt')
                if not prompt or isinstance(prompt, str) and not prompt.strip():
                    status['state'] = 'failed'; run['error'] = 'missing_prompt'; run['state'] = 'failed'; break
                from .video_models import ImageRequest
                request = ImageRequest(str(prompt), str(data.get('model') or 'grok-imagine-image'), aspect_ratio=str(data.get('aspect_ratio') or '') or None, n=data.get('n', 1))
                try:
                    generate = self._preflight('image', request)
                except Exception as exc:
                    status['state'] = 'failed'
                    run['error'] = _classified_provider_error(exc) or 'provider_preflight_failed'
                    run['state'] = 'failed'
                    break
                if not self._claim_submission(run, node_id):
                    return self.get(run_id)
                try:
                    job = generate()
                except Exception as exc:
                    code = _submission_rejection(exc)
                    if code is not None:
                        status.pop('submission_pending', None)
                        status['state'] = 'failed'
                        run['error'] = code
                        run['state'] = 'failed'
                        break
                    # An unknown transport failure may happen after the
                    # provider accepted a paid request. Keep it pending so a
                    # later tick can reconcile the job instead of duplicating
                    # the submission.
                    status['submission_pending'] = True
                    run['error'] = 'submission_unknown'
                    break
                status.pop('submission_pending', None)
                run['provider_jobs'][node_id] = job.provider_job_id
                image_urls = job.asset_urls or ([job.asset_url] if job.asset_url else [])
                if len(image_urls) > 10 or len(image_urls) > request.n:
                    status['state'] = 'failed'
                    run['error'] = 'invalid_provider_response'
                    run['state'] = 'failed'
                    break
                if image_urls:
                    try:
                        if self.asset_store is None and (len(image_urls) > 1 or request.n > 1):
                            raise VideoValidationError('asset unavailable')
                        run['assets'][node_id] = (
                            self._archive_images(image_urls) if self.asset_store else image_urls[0]
                        )
                    except Exception as exc:
                        status['state'] = 'failed'
                        run['error'] = 'asset_storage_full' if isinstance(exc, VideoStorageFullError) else _classified_provider_error(exc) or 'asset_archive_failed'
                        run['state'] = 'failed'
                        break
                if job.state == 'succeeded':
                    status['state'] = 'succeeded'
                continue
            image_url = self._source_value(run, node_id, 'image')
            if node_type == 'first_last_frame_video':
                first = self._source_value(run, node_id, 'first_frame')
                last = self._source_value(run, node_id, 'last_frame')
                if not first or not last:
                    status['state'] = 'failed'; run['error'] = 'missing_frame_selection'; run['state'] = 'failed'; break
                if first == last:
                    status['state'] = 'failed'; run['error'] = 'identical_frame_selection'; run['state'] = 'failed'; break
                image_url = first
                from .video_models import VideoRequest
                try:
                    first = self._provider_image(first)
                    last = self._provider_image(last)
                except VideoValidationError:
                    status['state'] = 'failed'; run['error'] = 'input_asset_unavailable'; run['state'] = 'failed'; break
                request = VideoRequest(str(data.get('prompt') or ''), str(data.get('model') or 'grok-imagine-video'), first_frame_url=first, last_frame_url=last, duration=int(data.get('duration') or 0) or None, aspect_ratio=str(data.get('aspect_ratio') or '') or None)
            else:
                if not image_url:
                    continue
                try:
                    image_url = self._provider_image(image_url)
                except VideoValidationError:
                    status['state'] = 'failed'; run['error'] = 'input_asset_unavailable'; run['state'] = 'failed'; break
                from .video_models import VideoRequest
                request = VideoRequest(str(data.get('prompt') or ''), str(data.get('model') or 'grok-imagine-video'), image_url=str(image_url), duration=int(data.get('duration') or 0) or None, aspect_ratio=str(data.get('aspect_ratio') or '') or None)
            try:
                generate = self._preflight('video', request)
            except Exception as exc:
                status['state'] = 'failed'
                run['error'] = _classified_provider_error(exc) or 'provider_preflight_failed'
                run['state'] = 'failed'
                break
            if not self._claim_submission(run, node_id):
                return self.get(run_id)
            try:
                job = generate()
            except Exception as exc:
                code = _submission_rejection(exc)
                if code is not None:
                    status.pop('submission_pending', None)
                    status['state'] = 'failed'
                    run['error'] = code
                    run['state'] = 'failed'
                    break
                status['submission_pending'] = True
                run['error'] = 'submission_unknown'
                break
            status.pop('submission_pending', None)
            run['provider_jobs'][node_id] = job.provider_job_id
            if job.asset_url:
                run['assets'][node_id] = job.asset_url
            if job.state == 'succeeded':
                status['state'] = 'succeeded'
        if run['state'] != 'failed' and all(item['state'] == 'succeeded' for item in run['node_status'].values()):
            run['state'] = 'succeeded'
        self._replace(run)
        return self.get(run_id)

    def cancel(self, run_id):
        run = self.get(run_id)
        if run is None:
            raise VideoValidationError('run not found')
        if any(status.get('submission_pending') for status in run.get('node_status', {}).values()):
            run['error'] = 'submission_unknown'
            return run
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
        records = self._records()
        # Claim uses monotonic submission versions and incarnation tokens, so
        # replay after a crash cannot reclaim a deleted node or a newer run.
        for run in records:
            self.workflows.reconcile_candidate_run(run, claim=True)
            self.workflows.reconcile_video_run(run, claim=True)
            self.workflows.reconcile_candidate_run(run)
            self.workflows.reconcile_video_run(run)
        return [run for run in records if run.get('state') in {'queued', 'running'}]
