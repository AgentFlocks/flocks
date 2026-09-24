"""Regression against the previous discovery contract on populated installs."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import os
import threading
import time

import pytest
from flocks.hub import catalog, local, tool_tree


@pytest.fixture(autouse=True)
def isolated():
    catalog.clear_catalog_caches()
    yield
    catalog.clear_catalog_caches()


def roots(monkeypatch, tmp_path):
    root = tmp_path / 'user-plugins'
    root.mkdir()
    monkeypatch.setattr(local, '_user_plugins_root', lambda: root)
    monkeypatch.setattr(local, '_project_plugins_root', lambda: tmp_path / 'project-plugins')
    monkeypatch.setattr(local, '_record_path', lambda: tmp_path / 'installed.json')
    return root


def legacy_tools(base):
    """Previous inference rules, retained solely as a compatibility oracle."""
    result = {}
    for child in base.iterdir():
        if child.is_dir() and local.has_install_payload('tool', child):
            result.setdefault(('tool', child.name), child)
    for group in ('api', 'device', 'mcp', 'generated', 'python'):
        folder = base / group
        if not folder.is_dir():
            continue
        for child in folder.iterdir():
            if child.is_dir() and local.has_install_payload('tool', child):
                result.setdefault(('device' if group == 'device' else 'tool', child.name), child)
            elif child.is_file() and child.suffix in {'.yaml', '.yml', '.py'} and local.has_install_payload('tool', child):
                result.setdefault(('tool', child.stem), child)
    for candidate in base.rglob('*'):
        if candidate.is_file() and candidate.name != '__init__.py' and candidate.suffix in {'.yaml', '.yml', '.py'}:
            if local.has_install_payload('tool', candidate.parent):
                if ('device', candidate.stem) not in result:
                    result.setdefault(('tool', candidate.stem), candidate.parent)
    return result


def payload(base, name, text='name: fixture'):
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_one_walk_large_install_matches_legacy_and_reuses_global_tree(monkeypatch, tmp_path):
    root = roots(monkeypatch, tmp_path)
    base = root / 'tools'
    for folder in range(32):
        for file in range(215):
            payload(base, f'api/large/lib{folder}/module_{folder}_{file}.py', '# fixture\n')
    payload(base, 'device/xdr/_provider.yaml')
    payload(base, 'api/large/blob.dat', 'x' * 2_000_000)
    original = os.scandir
    original_listdir = os.listdir
    lists = Counter()
    scans, phase = Counter(), ['legacy']
    def counted(path):
        if Path(path) == base or base in Path(path).parents:
            scans[(phase[0], str(path))] += 1
            time.sleep(.00005)  # deterministic per-directory latency, no live storage
        return original(path)
    monkeypatch.setattr(os, 'scandir', counted)
    def listdir(path):
        if Path(path) == base or base in Path(path).parents:
            lists[phase[0]] += 1
        return original_listdir(path)
    monkeypatch.setattr(os, 'listdir', listdir)
    probes = Counter()
    original_probe = local.has_install_payload
    def probe(kind, path):
        probes[(phase[0], kind, str(path))] += 1
        return original_probe(kind, path)
    monkeypatch.setattr(local, 'has_install_payload', probe)
    stats = Counter()
    original_stat = Path.stat
    def stat_path(path, *args, **kwargs):
        if path == base or base in path.parents:
            stats[phase[0]] += 1
            time.sleep(.00005)
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat_path)
    started = time.monotonic()
    expected = legacy_tools(base)
    baseline_seconds = time.monotonic() - started
    phase[0] = 'cold'
    started = time.monotonic()
    actual = local.infer_local_installs()
    cold_seconds = time.monotonic() - started
    assert actual == expected
    phase[0] = 'hot'
    assert local.infer_local_installs() == expected
    # A different project must reuse the same validated global tree.
    monkeypatch.setattr(local, '_project_plugins_root', lambda: tmp_path / 'other-project')
    assert local.infer_local_installs() == expected
    counts = Counter()
    for (kind, path), count in scans.items():
        counts[kind] += count
        if kind == 'cold':
            assert count == 1, f'repeated scan: {path}'
    assert counts['legacy'] > 13_000
    assert counts['cold'] <= 37 and counts['hot'] == 0
    phase[0] = 'catalog_cold'
    catalog.clear_catalog_caches()
    entries = catalog._catalog_entries_snapshot()
    assert len(entries) > 800
    phase[0] = 'catalog_hot'
    assert catalog._catalog_entries_snapshot() is entries
    catalog_cold = sum(n for (kind, _), n in scans.items() if kind == 'catalog_cold')
    catalog_hot = sum(n for (kind, _), n in scans.items() if kind == 'catalog_hot')
    assert catalog_cold <= 60 and catalog_hot <= 20
    assert not any(kind == 'catalog_hot' and '/lib' in path for kind, path in scans)
    print({'files': 6882, 'legacy_scans': counts['legacy'], 'cold_scans': counts['cold'],
           'hot_scans': counts['hot'], 'catalog_cold_scans': catalog_cold, 'catalog_hot_scans': catalog_hot, 'legacy_payload_calls': sum(n for (kind, _, _), n in probes.items() if kind == 'legacy'), 'legacy_unique_payload_paths': len({path for (kind, _, path) in probes if kind == 'legacy'}), 'installed_tree_stat_calls': dict(stats), 'installed_tree_listdir_calls': dict(lists), 'legacy_seconds': round(baseline_seconds, 3), 'cold_seconds': round(cold_seconds, 3)})


def test_nested_delete_add_and_permissions_invalidate(monkeypatch, tmp_path):
    root = roots(monkeypatch, tmp_path)
    base = root / 'tools'
    file = payload(base, 'api/package/nested/query.py')
    before = local.tool_discovery_signature()
    assert ('tool', 'package') in local.infer_local_installs()
    file.unlink()
    assert local.tool_discovery_signature() != before
    assert ('tool', 'package') not in local.infer_local_installs()
    payload(base, 'api/package/nested/new.py')
    assert ('tool', 'new') in local.infer_local_installs()
    tool_tree.clear()
    original = os.scandir
    def denied(path):
        if Path(path).name == 'nested':
            raise PermissionError('fixture denied')
        return original(path)
    monkeypatch.setattr(os, 'scandir', denied)
    with pytest.raises(PermissionError):
        local.infer_local_installs()
    assert not tool_tree._flights
    monkeypatch.setattr(os, 'scandir', original)
    assert ('tool', 'new') in local.infer_local_installs()


def test_canonical_symlinks_and_legacy_aliases_match(monkeypatch, tmp_path):
    root = roots(monkeypatch, tmp_path)
    base = root / 'tools'
    payload(base, 'api/only_yml/query.yml')
    payload(base, 'api/init/__init__.py')
    payload(base, 'api/one/shared.py')
    payload(base, 'api/two/shared.py')
    payload(base, 'device/xdr/_provider.yaml')
    payload(base, 'python/direct.yml')
    outside = tmp_path / 'linked-package'
    linked = payload(outside, 'nested/action.py')
    (base / 'device' / 'linked').symlink_to(outside, target_is_directory=True)
    (base / 'api' / 'broken').symlink_to(tmp_path / 'missing')
    (base / 'api' / 'one' / 'cycle').symlink_to(base, target_is_directory=True)
    assert local.infer_local_installs() == legacy_tools(base)
    signature = local.tool_discovery_signature()
    linked.unlink()
    assert signature != local.tool_discovery_signature()
    assert ('device', 'linked') not in local.infer_local_installs()
    alias = tmp_path / 'root-link'
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(local, '_user_plugins_root', lambda: alias)
    assert local.infer_local_installs() == legacy_tools(alias / 'tools')


def test_concurrent_projects_share_scan_and_refresh_stops_old_scan(monkeypatch, tmp_path):
    base = tmp_path / 'tools'
    for i in range(100):
        payload(base, f'api/package/sub{i}/query.py')
    entered, release = threading.Event(), threading.Event()
    original = os.scandir
    calls = Counter()
    first = [True]
    @contextmanager
    def gated(path):
        calls[str(path)] += 1
        if Path(path) == base and first[0]:
            first[0] = False
            entered.set()
            assert release.wait(3)
        with original(path) as entries:
            yield entries
    monkeypatch.setattr(os, 'scandir', gated)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(tool_tree.get, base) for _ in range(4)]
        assert entered.wait(1)
        tool_tree.clear()
        release.set()
        values = [job.result(timeout=3) for job in jobs]
    assert all(value.payload[base] for value in values)
    # Superseded work stops at its next directory entry, not after 100 folders.
    assert calls[str(base)] == 2
    assert all(count == 1 for path, count in calls.items() if path != str(base))
    assert not tool_tree._flights


def test_missing_payloads_do_not_become_installed(monkeypatch, tmp_path):
    root = roots(monkeypatch, tmp_path)
    base = root / 'tools'
    for i in range(150):
        payload(base, f'api/yml_only/query_{i}.yml')
    payload(base, 'api/init_only/__init__.py')
    payload(base, 'api/data_only/large.bin', 'x' * 1_000_000)
    assert local.infer_local_installs() == legacy_tools(base) == {}
    absent = base / 'absent'
    probes = []
    original = local._probe_install_payload
    def probe(kind, path):
        probes.append(path)
        return original(kind, path)
    monkeypatch.setattr(local, '_probe_install_payload', probe)
    with local.discovery_scope():
        assert not local.has_install_payload('tool', absent)
        assert not local.has_install_payload('tool', absent)
    assert probes == [absent]
    # Negative presence is scoped to one build; subsequent installation wins.
    payload(absent, 'query.py')
    assert local.has_install_payload('tool', absent)
    assert ('tool', 'absent') in local.infer_local_installs()


def test_catalog_rechecks_deep_payload_and_record_after_external_delete(monkeypatch, tmp_path):
    roots(monkeypatch, tmp_path)
    plugin_id = 'soc_workspace_query'
    manifest = catalog.load_manifest('tool', plugin_id)
    directory = local.install_dir('tool', plugin_id)
    file = payload(directory, 'nested/query.py')
    local.save_installed_record(local.make_record(plugin_type='tool', plugin_id=plugin_id,
        version=manifest.version, source='bundled:fixture', install_path=directory))
    def entry():
        return next(item for item in catalog._catalog_entries_snapshot() if item.type == 'tool' and item.id == plugin_id)
    assert entry().state == 'installed'
    file.unlink()  # canonical directory mtime does not change; its child does
    assert entry().state == 'available'
    assert local.get_record('tool', plugin_id) is None
    payload(directory, 'nested/restored.py')
    assert entry().state == 'updateAvailable'  # real payload, unknown legacy version
    catalog.clear_catalog_caches()
    assert entry().state == 'updateAvailable'
