import json
import math
from pathlib import Path
import time

import pytest

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


def test_strict_json_load_only_defaults_for_missing_files(tmp_path):
    missing = tmp_path / 'missing.json'
    assert state_store.load_json_strict(missing, {}) == {}
    with pytest.raises(state_store.InvalidJsonState, match='missing required'):
        state_store.load_json_strict(missing, {}, required=True)

    corrupt = tmp_path / 'corrupt.json'
    corrupt.write_text('{broken', encoding='utf-8')
    with pytest.raises(state_store.InvalidJsonState):
        state_store.load_json_strict(corrupt, {})
    assert corrupt.read_text(encoding='utf-8') == '{broken'

    wrong_shape = tmp_path / 'wrong-shape.json'
    wrong_shape.write_text('[]', encoding='utf-8')
    with pytest.raises(state_store.InvalidJsonState):
        state_store.load_json_strict(wrong_shape, {})


def test_directory_fsync_failure_is_not_reported_as_durable(
    tmp_path, monkeypatch
):
    target = tmp_path / 'state.json'

    def fail_directory_fsync(_path):
        raise OSError('simulated directory fsync failure')

    monkeypatch.setattr(
        state_store,
        '_fsync_dir',
        fail_directory_fsync,
    )

    with pytest.raises(
        state_store.AtomicReplaceDurabilityUncertain,
        match='directory durability is uncertain',
    ) as raised:
        state_store.save_json(target, {'committed': True})

    # os.replace may already have happened.  The critical contract is that the
    # caller is told durability is uncertain instead of receiving false
    # success.
    assert raised.value.path == target
    assert isinstance(raised.value.__cause__, OSError)
    assert 'directory fsync failure' in str(raised.value.__cause__)
    assert json.loads(target.read_text(encoding='utf-8')) == {
        'committed': True,
    }


def test_fsync_dir_propagates_open_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        state_store.os,
        'open',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError('cannot open directory')
        ),
    )

    with pytest.raises(OSError, match='cannot open directory'):
        state_store._fsync_dir(tmp_path)


def test_file_lock_timeout_bounds_contention(tmp_path):
    lock_path = tmp_path / 'state.lock'

    with state_store.file_lock(lock_path):
        started = time.monotonic()
        with pytest.raises(
            state_store.LockTimeout,
            match='timed out acquiring state lock',
        ):
            with state_store.file_lock(
                lock_path,
                timeout=0.05,
                poll_interval=0.005,
            ):
                pytest.fail('contended lock must not be acquired')
        elapsed = time.monotonic() - started

    assert 0.04 <= elapsed < 0.5
    with state_store.file_lock(lock_path, timeout=0.05):
        pass


@pytest.mark.parametrize(
    'timeout',
    (-1, True, math.inf, math.nan, '1'),
)
def test_file_lock_rejects_invalid_timeout(tmp_path, timeout):
    with pytest.raises(ValueError):
        with state_store.file_lock(
            tmp_path / 'invalid.lock',
            timeout=timeout,
        ):
            pass
