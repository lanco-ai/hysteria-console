import pytest

from web_api.video_service import WorkflowStore, VideoValidationError, validate_workflow


def node(node_id, node_type):
    return {'id': node_id, 'type': node_type, 'data': {}}


def edge(source, source_port, target, target_port):
    return {
        'id': f'{source}-{target}', 'source': source, 'sourceHandle': source_port,
        'target': target, 'targetHandle': target_port,
    }


def test_validate_workflow_returns_topological_order():
    result = validate_workflow(
        nodes=[node('prompt', 'prompt'), node('image', 'text_to_image'), node('preview', 'preview')],
        edges=[edge('prompt', 'text', 'image', 'prompt'), edge('image', 'image', 'preview', 'media')],
    )
    assert result.order == ['prompt', 'image', 'preview']


def test_validate_workflow_rejects_cycles_and_missing_inputs():
    with pytest.raises(VideoValidationError, match='cycle'):
        validate_workflow(
            nodes=[node('a', 'prompt'), node('b', 'prompt')],
            edges=[edge('a', 'text', 'b', 'text'), edge('b', 'text', 'a', 'text')],
        )
    with pytest.raises(VideoValidationError, match='prompt'):
        validate_workflow(nodes=[node('image', 'text_to_image')], edges=[])


def test_workflow_store_round_trips_and_deletes(tmp_path):
    store = WorkflowStore(tmp_path / 'workflows.json')
    saved = store.save({'title': 'demo', 'nodes': [node('p', 'prompt')], 'edges': []})
    assert saved['id']
    assert store.get(saved['id'])['title'] == 'demo'
    assert store.list()[0]['id'] == saved['id']
    assert store.delete(saved['id']) is True
    assert store.get(saved['id']) is None


def test_workflow_store_rejects_api_key_in_graph(tmp_path):
    store = WorkflowStore(tmp_path / 'workflows.json')
    with pytest.raises(VideoValidationError):
        store.save({'nodes': [], 'edges': [], 'metadata': {'api_key': 'secret'}})
