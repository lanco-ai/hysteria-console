import json
from pathlib import Path

import state_store


def test_save_json_uses_unique_temp_file(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    seen = []
    real_replace = state_store.os.replace

    def record_replace(src, dst):
        seen.append(Path(src).name)
        return real_replace(src, dst)

    monkeypatch.setattr(state_store.os, 'replace', record_replace)

    state_store.save_json(target, {'a': 1})
    state_store.save_json(target, {'a': 2})

    assert json.loads(target.read_text()) == {'a': 2}
    assert len(seen) == 2
    assert all(name.startswith('state.json.') and name.endswith('.tmp') for name in seen)
    assert all(name != 'state.json.tmp' for name in seen)
    assert not list(tmp_path.glob('*.tmp'))
