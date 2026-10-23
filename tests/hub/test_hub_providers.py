"""Hub plugins an extension provides (flocks.hub.providers).

Case list (编号 | 场景):
  HP1 a provider's plugins are listed from its manifests, under its own kind
      of source, and supersede a hub root's listing of the same id
  HP2 an install stages the provider's files for the operation only and
      records the provider's kind of source
  HP3 outside an operation a plugin the provider claims has no files to
      install or preview from -- not another listing of the same id
  HP4 a provider that cannot answer leaves its plugins out of the listings,
      and what it claims still does not install from elsewhere
  HP5 a stage that fails stops the install before anything is recorded
  HP6 the manifest staged is the one recorded, whatever the provider lists
      meanwhile
  HP7 a nested operation (a suite installing its parts) uses the outer stage
  HP8 a claim the provider cannot answer counts as a claim
  HP9 an unregistered provider's plugins leave the catalog
  HP10 another provider's copy of a plugin does not stand in for the provider
       that claims it while that one cannot deliver it: the operation is refused
  HP11 of two providers listing the same plugin the first registered one's copy
       counts; when it stages nothing of it the other's is not used either
  HP12 a provider that cannot tell whether it claims a plugin blocks other copies
  HP13 a provider that neither lists nor claims a plugin leaves it to the next
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from flocks.extensions import register_hub_package_provider, register_hub_root, unregister_hub_package_provider, unregister_hub_root
from flocks.hub import catalog, installer, local, providers
from flocks.hub.files import file_tree, plugin_root
from flocks.hub.models import HubPluginManifest

SKILL = "acme-skill"


def _manifest(version: str, *, kind: str = "archive") -> HubPluginManifest:
    return HubPluginManifest.model_validate({
        "schemaVersion": "hub.plugin.v1", "id": SKILL, "type": "skill", "name": SKILL,
        "description": "delivered by a provider", "version": version, "category": "integration",
        "trust": "community", "edition": "acme",
        "source": {"kind": kind, "path": f"plugins/skills/{SKILL}"}, "entrypoints": ["SKILL.md"],
    })


class _ArchiveProvider:
    """Delivers one skill from an archive of its own, unpacked only for an operation."""

    def __init__(self, workdir: Path, *, name: str = "test-archive", kind: str = "archive", version: str = "1.0.0") -> None:
        self.name = name
        self.kind = kind
        self.workdir = workdir
        self.version = version
        self.unavailable = False
        self.claims_it = True
        self.stages_nothing = False
        self.stage_error: str | None = None
        self.claims_error = False
        self.entered: list[tuple[str, str]] = []
        self.exited = 0
        self.during_stage = None

    def signature(self) -> str:
        return "unavailable" if self.unavailable else f"{SKILL}@{self.version}"

    def plugins(self):
        if self.unavailable:
            raise providers.ProviderUnavailable("the archive cannot be read")
        return {("skill", SKILL): _manifest(self.version, kind=self.kind)}

    def claims(self, plugin_type: str, plugin_id: str) -> bool:
        if self.claims_error:
            raise RuntimeError("cannot tell")
        return self.claims_it and (plugin_type, plugin_id) == ("skill", SKILL)

    @asynccontextmanager
    async def stage(self, plugin_type: str, plugin_id: str):
        self.entered.append((plugin_type, plugin_id))
        if self.stage_error:
            raise RuntimeError(self.stage_error)
        if (plugin_type, plugin_id) != ("skill", SKILL) or self.unavailable or self.stages_nothing:
            yield {}
            return
        manifest = _manifest(self.version, kind=self.kind)
        root = self.workdir / f"stage-{len(self.entered)}" / "plugins" / "skills" / SKILL
        root.mkdir(parents=True)
        (root / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
        (root / "SKILL.md").write_text(f"---\nname: {SKILL}\ndescription: staged\n---\n# {SKILL} {manifest.version}\n", encoding="utf-8")
        if self.during_stage is not None:
            self.during_stage()
        try:
            yield {("skill", SKILL): (root, manifest)}
        finally:
            self.exited += 1


@pytest.fixture
def hub_env(isolated_hub_env, monkeypatch):
    async def refresh(*args, **kwargs):
        pass

    monkeypatch.setattr(installer, "_refresh_runtime", refresh)
    catalog.clear_catalog_caches()
    yield isolated_hub_env
    catalog.clear_catalog_caches()


@pytest.fixture
def provider(hub_env, tmp_path):
    created = _ArchiveProvider(tmp_path / "archive")
    register_hub_package_provider(created)
    try:
        yield created
    finally:
        unregister_hub_package_provider(created)


def _entries():
    return {(entry.type, entry.id): entry for entry in catalog.list_catalog()}


def _plain_listing(root: Path) -> None:
    """The same skill in plain, in a hub root of its own."""
    folder = root / "plugins" / "skills" / SKILL
    folder.mkdir(parents=True)
    manifest = _manifest("9.9.9", kind="bundled").model_dump(mode="json")
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "SKILL.md").write_text("plain copy\n", encoding="utf-8")
    (root / "index.json").write_text(json.dumps({"schemaVersion": "hub.index.v1", "plugins": [
        {"id": SKILL, "type": "skill", "name": SKILL, "version": "9.9.9",
         "manifestPath": f"plugins/skills/{SKILL}/manifest.json"},
    ]}), encoding="utf-8")


# HP1
def test_provided_plugins_are_listed_from_their_manifests(provider, tmp_path):
    _plain_listing(tmp_path / "plain")
    register_hub_root("hp1-plain", tmp_path / "plain")
    try:
        entry = _entries()[("skill", SKILL)]
        assert (entry.source, entry.version, entry.edition) == ("archive", "1.0.0", "acme")
        assert entry.manifestPath == f"plugins/skills/{SKILL}/manifest.json"
        assert catalog.load_manifest("skill", SKILL).version == "1.0.0"
    finally:
        unregister_hub_root("hp1-plain")


# HP2
@pytest.mark.asyncio
async def test_an_install_draws_on_the_stage_and_records_the_kind(provider, hub_env):
    record = await installer.install_plugin("skill", SKILL)

    assert provider.entered == [("skill", SKILL)] and provider.exited == 1
    assert record.source == f"archive:plugins/skills/{SKILL}"
    installed = hub_env["home"] / ".flocks" / "plugins" / "skills" / SKILL / "SKILL.md"
    assert "acme-skill 1.0.0" in installed.read_text(encoding="utf-8")
    assert _entries()[("skill", SKILL)].state == "installed"


# HP3
def test_a_claimed_plugin_has_no_files_outside_an_operation(provider, tmp_path):
    _plain_listing(tmp_path / "plain")
    register_hub_root("hp3-plain", tmp_path / "plain")
    try:
        with pytest.raises(FileNotFoundError, match="delivered by an extension"):
            plugin_root("skill", SKILL, prefer_bundled=True)
        with pytest.raises(FileNotFoundError, match="delivered by an extension"):
            file_tree("skill", SKILL)
    finally:
        unregister_hub_root("hp3-plain")


@pytest.mark.asyncio
async def test_the_stage_is_where_the_files_are_while_it_lasts(provider):
    async with providers.operation("skill", SKILL):
        staged = plugin_root("skill", SKILL, prefer_bundled=True)
        assert (staged / "SKILL.md").is_file()
        assert providers.staged_manifest("skill", SKILL).version == "1.0.0"
    assert providers.staged_root("skill", SKILL) is None
    assert provider.exited == 1


# HP4
@pytest.mark.asyncio
async def test_an_unanswering_provider_lists_nothing_and_installs_nothing(provider, tmp_path):
    _plain_listing(tmp_path / "plain")
    register_hub_root("hp4-plain", tmp_path / "plain")
    provider.unavailable = True
    try:
        entry = _entries()[("skill", SKILL)]
        assert (entry.source, entry.version) == ("bundled", "9.9.9")  # the plain listing, for display only
        with pytest.raises(FileNotFoundError, match="delivered by an extension"):
            await installer.install_plugin("skill", SKILL)
        assert local.get_record("skill", SKILL) is None
    finally:
        unregister_hub_root("hp4-plain")


# HP5
@pytest.mark.asyncio
async def test_a_failed_stage_records_nothing(provider):
    provider.stage_error = "the archive is damaged"
    with pytest.raises(RuntimeError, match="damaged"):
        await installer.install_plugin("skill", SKILL)
    assert local.get_record("skill", SKILL) is None


# HP6
@pytest.mark.asyncio
async def test_the_staged_manifest_is_the_one_recorded(provider):
    def newer_release():
        provider.version = "1.2.0"
        providers.clear_cache()

    provider.during_stage = newer_release
    await installer.install_plugin("skill", SKILL)
    assert local.get_record("skill", SKILL).version == "1.0.0"
    assert _entries()[("skill", SKILL)].state == "updateAvailable"


# HP7
@pytest.mark.asyncio
async def test_a_nested_operation_uses_the_outer_stage(provider):
    async with providers.operation("skill", SKILL):
        outer = providers.staged_root("skill", SKILL)
        async with providers.operation("skill", SKILL):
            assert providers.staged_root("skill", SKILL) == outer
    assert provider.entered == [("skill", SKILL)]


# HP8
def test_a_claim_that_cannot_be_answered_counts(provider):
    provider.unavailable = True
    provider.claims_error = True
    assert providers.claimed("skill", "anything") is True


# HP9
def test_an_unregistered_provider_leaves_the_catalog(hub_env, tmp_path):
    created = _ArchiveProvider(tmp_path / "archive")
    register_hub_package_provider(created)
    assert ("skill", SKILL) in _entries()
    unregister_hub_package_provider(created)
    assert ("skill", SKILL) not in _entries()
    assert providers.signature() == ""


@pytest.fixture
def mirror(provider, tmp_path):
    """A second provider, registered after the first, with its own copy of the same skill."""
    created = _ArchiveProvider(tmp_path / "mirror", name="test-mirror", kind="mirror", version="6.6.6")
    register_hub_package_provider(created)
    try:
        yield created
    finally:
        unregister_hub_package_provider(created)


# HP10
@pytest.mark.asyncio
async def test_another_copy_does_not_stand_in_for_a_claimed_plugin(provider, mirror):
    provider.unavailable = True  # it still claims the skill, and stages nothing of it now

    with pytest.raises(providers.ProviderUnavailable, match="test-archive"):
        async with providers.operation("skill", SKILL):
            pytest.fail("the operation must not start on the mirror's copy")
    with pytest.raises(providers.ProviderUnavailable, match="test-archive"):
        await installer.install_plugin("skill", SKILL)

    assert local.get_record("skill", SKILL) is None
    assert mirror.entered and mirror.exited == len(mirror.entered)  # its stage was cleaned up all the same
    assert providers.staged_root("skill", SKILL) is None


# HP11
@pytest.mark.asyncio
async def test_the_first_provider_listing_a_plugin_is_the_one_staged(provider, mirror):
    async with providers.operation("skill", SKILL):
        assert (providers.staged_manifest("skill", SKILL).version, providers.staged_manifest("skill", SKILL).source.kind) == ("1.0.0", "archive")

    provider.stages_nothing = True
    with pytest.raises(providers.ProviderUnavailable, match="test-archive"):
        async with providers.operation("skill", SKILL):
            pytest.fail("the operation must not start on the mirror's copy")


# HP12
@pytest.mark.asyncio
async def test_a_claim_that_cannot_be_answered_blocks_other_copies(provider, mirror):
    provider.unavailable = True
    provider.claims_error = True

    with pytest.raises(providers.ProviderUnavailable, match="cannot tell"):
        async with providers.operation("skill", SKILL):
            pytest.fail("the operation must not start on the mirror's copy")


# HP13
@pytest.mark.asyncio
async def test_a_provider_that_does_not_deliver_a_plugin_leaves_it_to_the_next(provider, mirror):
    provider.unavailable = True
    provider.claims_it = False

    async with providers.operation("skill", SKILL):
        staged = providers.staged_manifest("skill", SKILL)
        assert (staged.version, staged.source.kind) == ("6.6.6", "mirror")
