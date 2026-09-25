"""Catalog contention, invalidation and root isolation without live user data."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest

from flocks.hub import catalog


@pytest.fixture(autouse=True)
def isolated():
    catalog.clear_catalog_caches()
    yield
    catalog.clear_catalog_caches()


def track_wait(monkeypatch):
    waiting = threading.Event()
    original = catalog.Future

    class ObservedFuture(original):
        def result(self, timeout=None):
            waiting.set()
            return super().result(timeout=timeout)

    monkeypatch.setattr(catalog, 'Future', ObservedFuture)
    return waiting


def test_slow_cold_build_does_not_block_hot_or_unrelated_root(monkeypatch):
    state = threading.local()
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(catalog, '_catalog_entries_cache_key', lambda: ((state.root, 1, 1),))
    def build(signature):
        root = signature[0][0]
        if root == 'slow':
            entered.set()
            assert release.wait(3)
        return (root,)
    monkeypatch.setattr(catalog, '_build_catalog_entries', build)
    def read(root):
        state.root = root
        return catalog._catalog_entries_snapshot()
    assert read('hot') == ('hot',)
    with ThreadPoolExecutor(max_workers=3) as pool:
        slow = pool.submit(read, 'slow')
        try:
            assert entered.wait(1)
            assert pool.submit(read, 'hot').result(timeout=.5) == ('hot',)
            assert pool.submit(read, 'different-user-root').result(timeout=.5) == ('different-user-root',)
            assert not slow.done()
        finally:
            release.set()
        assert slow.result(timeout=1) == ('slow',)


def test_same_signature_concurrent_reads_build_once(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    waiting = track_wait(monkeypatch)
    calls = []
    monkeypatch.setattr(catalog, '_catalog_entries_cache_key', lambda: ())
    def build(signature):
        calls.append(signature)
        entered.set()
        assert release.wait(3)
        return ('complete',)
    monkeypatch.setattr(catalog, '_build_catalog_entries', build)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [pool.submit(catalog._catalog_entries_snapshot) for _ in range(4)]
        try:
            assert entered.wait(1) and waiting.wait(1)
            assert len(calls) == 1
        finally:
            release.set()
        assert [r.result(timeout=1) for r in results] == [('complete',)] * 4
    assert len(calls) == 1 and not catalog._CATALOG_BUILDS


def test_refresh_wakes_waiters_and_never_publishes_old_build(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    waiting = track_wait(monkeypatch)
    old_generation = catalog._CATALOG_GENERATION
    generations = []
    monkeypatch.setattr(catalog, '_catalog_entries_cache_key', lambda: ())
    def build(signature):
        generation = catalog._cache_generation()
        generations.append(generation)
        if generation == old_generation:
            entered.set()
            assert release.wait(3)
            # A refresh must not make an old build use the new cache epoch.
            assert catalog._cache_generation() == old_generation
            return ('old',)
        return ('new-install-state',)
    monkeypatch.setattr(catalog, '_build_catalog_entries', build)
    with ThreadPoolExecutor(max_workers=3) as pool:
        old = pool.submit(catalog._catalog_entries_snapshot)
        assert entered.wait(1)
        waiter = pool.submit(catalog._catalog_entries_snapshot)
        try:
            assert waiting.wait(1)
            catalog.clear_catalog_caches()
            assert pool.submit(catalog._catalog_entries_snapshot).result(timeout=.5) == ('new-install-state',)
            assert waiter.result(timeout=.5) == ('new-install-state',)
            assert not old.done()
        finally:
            release.set()
        assert old.result(timeout=1) == ('new-install-state',)
    assert generations == [old_generation, old_generation + 1]
    assert catalog._catalog_entries_snapshot() == ('new-install-state',)
    assert not catalog._CATALOG_BUILDS


def test_builder_failure_unblocks_joined_request_and_next_read_recovers(monkeypatch):
    release = threading.Event()
    waiting = track_wait(monkeypatch)
    fail = True
    monkeypatch.setattr(catalog, '_catalog_entries_cache_key', lambda: ())
    def build(signature):
        if fail:
            assert release.wait(3)
            raise ValueError('fixture failure')
        return ('recovered',)
    monkeypatch.setattr(catalog, '_build_catalog_entries', build)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(catalog._catalog_entries_snapshot) for _ in range(2)]
        try:
            assert waiting.wait(1)
        finally:
            release.set()
        for result in results:
            with pytest.raises(ValueError, match='fixture failure'):
                result.result(timeout=1)
    fail = False
    assert not catalog._CATALOG_BUILDS
    assert catalog._catalog_entries_snapshot() == ('recovered',)


def test_snapshot_lru_is_bounded_and_signature_change_rebuilds(monkeypatch):
    marker, calls = 0, []
    monkeypatch.setattr(catalog, '_catalog_entries_cache_key', lambda: ((str(marker), marker, marker),))
    def build(signature):
        calls.append(signature)
        return (signature,)
    monkeypatch.setattr(catalog, '_build_catalog_entries', build)
    for marker in range(12):
        assert catalog._catalog_entries_snapshot()[0][0][0] == str(marker)
    assert len(catalog._CATALOG_SNAPSHOTS) == 8
    catalog._catalog_entries_snapshot()
    assert len(calls) == 12
    marker = 0
    catalog._catalog_entries_snapshot()
    assert len(calls) == 13


def test_index_taxonomy_and_manifest_lookup_are_root_scoped(tmp_path, monkeypatch):
    roots = [tmp_path / 'a', tmp_path / 'b']
    for i, root in enumerate(roots):
        root.mkdir()
        (root / 'index.json').write_text(json.dumps({'schemaVersion': 'hub.index.v1', 'plugins': []}))
        (root / 'taxonomy.json').write_text(json.dumps({'schemaVersion': 'hub.taxonomy.v1', 'categories': []}))
    # Equal size/timestamps are not enough: the root path must be part of keys.
    chosen = roots[0]
    monkeypatch.setattr(catalog, 'get_bundled_hub_root', lambda: chosen)
    first = catalog.load_index()
    taxonomy = catalog.load_taxonomy()
    chosen = roots[1]
    assert catalog.load_index() is not first
    assert catalog.load_taxonomy() is not taxonomy
    assert catalog._manifest_path_lookup() == {}
    chosen = roots[0]
    assert catalog.load_index() is first


def test_build_memo_is_local_and_safe_yaml_rejects_python_tags(tmp_path):
    path = tmp_path / 'fixture.yaml'
    path.write_text('name: first\nnested: &value {enabled: true}\ncopy: *value\n')
    token = catalog._BUILD_MEMO.set({})
    try:
        first = catalog._read_yaml(path)
        path.write_text('name: second\n')
        assert catalog._read_yaml(path) is first
    finally:
        catalog._BUILD_MEMO.reset(token)
    assert catalog._read_yaml(path)['name'] == 'second'
    assert first['nested'] == first['copy'] == {'enabled': True}
    import yaml
    with pytest.raises(yaml.constructor.ConstructorError):
        catalog._safe_yaml('!!python/object/apply:os.system ["false"]')


def test_background_builder_never_holds_global_lock(monkeypatch):
    def build(_):
        acquired = catalog._CATALOG_ENTRIES_LOCK.acquire(blocking=False)
        assert acquired, 'catalog construction must run outside the cache lock'
        catalog._CATALOG_ENTRIES_LOCK.release()
        return ()

    monkeypatch.setattr(catalog, '_catalog_entries_cache_key', lambda: ())
    monkeypatch.setattr(catalog, '_build_catalog_entries', build)
    assert catalog._catalog_entries_snapshot() == ()
