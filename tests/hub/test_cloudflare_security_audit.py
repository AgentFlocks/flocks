"""Exercise the bundled Cloudflare skill through the real Hub install path."""

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from flocks.hub import catalog, local, paths
from flocks.hub.files import plugin_root
from flocks.hub.installer import install_plugin
from flocks.hub.security import SKIP_NAMES, validate_package
from flocks.project.instance import Instance
from flocks.skill.skill import Skill


SKILL_ID = "cloudflare-security-audit"
UPSTREAM_COMMIT = "c1c8a8c1471069fb0e188eeaff69b8e8db6564a8"
HUB_ROOT = Path(__file__).resolve().parents[2] / ".flocks" / "flockshub"


@pytest.fixture()
def cloudflare_env(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    """Keep runtime discovery and install records inside the temporary home."""
    project = str(isolated_hub_env["project_dir"])
    monkeypatch.setenv("FLOCKS_ROOT", str(isolated_hub_env["home"] / ".flocks"))
    monkeypatch.setenv("FLOCKS_HUB_ROOT", str(HUB_ROOT))
    monkeypatch.setattr(Instance, "get_directory", classmethod(lambda cls: project))
    monkeypatch.setattr(Instance, "get_worktree", classmethod(lambda cls: project))
    monkeypatch.setattr(paths, "_EXTENSION_HUB_ROOTS", {})
    catalog.clear_catalog_caches()
    Skill.clear_cache()
    yield isolated_hub_env
    catalog.clear_catalog_caches()
    Skill.clear_cache()


def _payload_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path.relative_to(root).as_posix() != "manifest.json"
        and not any(part in SKIP_NAMES for part in path.relative_to(root).parts)
    }


def _catalog_entry():
    entries = [
        entry
        for entry in catalog.list_catalog(plugin_type="skill")
        if entry.id == SKILL_ID
    ]
    assert len(entries) == 1
    return entries[0]


def test_package_is_pinned_and_every_offline_resource_is_checksummed(cloudflare_env):
    manifest = catalog.load_manifest("skill", SKILL_ID)
    package = plugin_root("skill", SKILL_ID, prefer_bundled=True)
    assert manifest.id == SKILL_ID and manifest.edition == "oss"
    assert UPSTREAM_COMMIT in json.dumps(manifest.model_dump())
    assert "SKILL.md" in manifest.entrypoints
    assert set(manifest.checksums) == _payload_files(package)
    validate_package(package, manifest)

    provenance = json.loads((package / "upstream.json").read_text(encoding="utf-8"))
    assert provenance["commit"] == UPSTREAM_COMMIT
    upstream_files = {entry["path"]: entry for entry in provenance["files"]}
    assert {
        "LICENSE", "AI-AND-LLM.md", "report-schema.json",
        "validate-findings.cjs", "validate-coverage-ledger.cjs",
    } <= upstream_files.keys()
    for relative, source in upstream_files.items():
        resource = package / relative
        assert resource.is_file(), f"Missing pinned upstream resource: {relative}"
        if relative not in provenance["modifiedFiles"]:
            assert hashlib.sha256(resource.read_bytes()).hexdigest() == source["sha256"]

    local_references = []
    for document in package.rglob("*.md"):
        targets = re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", document.read_text(encoding="utf-8"))
        for target in targets:
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resource = (document.parent / unquote(parsed.path)).resolve()
            assert resource.is_relative_to(package), f"Reference leaves the package: {document}: {target}"
            assert resource.is_file(), f"Missing offline resource: {document}: {target}"
            local_references.append(resource)
    assert local_references, "The audit guidance must link to its bundled resources"


async def test_install_refreshes_discovery_and_preserves_offline_payload(cloudflare_env):
    from flocks.tool.registry import ToolContext
    from flocks.tool.skill.skill_load import skill_tool_impl

    assert _catalog_entry().state == "available"
    # Prime discovery before installation to exercise the real cache refresh.
    assert await Skill.get(SKILL_ID) is None
    package = plugin_root("skill", SKILL_ID, prefer_bundled=True)
    record = await install_plugin("skill", SKILL_ID)

    destination = cloudflare_env["home"] / ".flocks" / "plugins" / "skills" / SKILL_ID
    assert Path(record.installPath) == destination
    assert local.get_record("skill", SKILL_ID) == record
    assert _catalog_entry().state == "installed"
    assert not (destination / "manifest.json").exists()
    assert _payload_files(destination) == _payload_files(package)
    for relative in _payload_files(package):
        assert (destination / relative).read_bytes() == (package / relative).read_bytes()

    skill = await Skill.get(SKILL_ID)
    assert skill is not None and skill.source == "user"
    assert Path(skill.location) == destination / "SKILL.md"
    result = await skill_tool_impl(
        ToolContext(session_id="cloudflare-test", message_id="load-skill"), SKILL_ID
    )
    assert result.success
    assert result.metadata["dir"] == str(destination)
    assert (destination / "SKILL.md").read_text(encoding="utf-8").strip() in result.output


async def test_installed_skill_reports_missing_node_before_runtime_is_available(
    cloudflare_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    await install_plugin("skill", SKILL_ID)
    skill = await Skill.get(SKILL_ID)
    assert skill is not None and skill.requires is not None
    assert "node" in (skill.requires.bins or [])

    runtime_bin = tmp_path / "runtime-bin"
    runtime_bin.mkdir()
    monkeypatch.setenv("PATH", str(runtime_bin))
    unavailable = Skill.check_eligibility(skill)
    assert unavailable.eligible is False
    assert "bin:node" in unavailable.missing

    # Eligibility only probes PATH; this fixture is deliberately never executed.
    executable = runtime_bin / ("node.exe" if os.name == "nt" else "node")
    executable.write_text("node-presence-test-fixture\n", encoding="utf-8")
    executable.chmod(0o755)
    available = Skill.check_eligibility(skill)
    assert available.eligible is True
    assert available.missing == []


async def test_corrupt_package_is_rejected_before_any_skill_is_installed(
    cloudflare_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Corrupt a disposable bundle, never the checked-in or installed package."""
    index = json.loads((HUB_ROOT / "index.json").read_text(encoding="utf-8"))
    index["plugins"] = [
        entry for entry in index["plugins"]
        if entry["type"] == "skill" and entry["id"] == SKILL_ID
    ]
    assert len(index["plugins"]) == 1
    bundle = tmp_path / "corrupted-hub"
    package = bundle / Path(index["plugins"][0]["manifestPath"]).parent
    shutil.copytree(plugin_root("skill", SKILL_ID, prefer_bundled=True), package)
    (bundle / "index.json").write_text(json.dumps(index), encoding="utf-8")
    shutil.copyfile(HUB_ROOT / "taxonomy.json", bundle / "taxonomy.json")
    with (package / "SKILL.md").open("a", encoding="utf-8") as handle:
        handle.write("\nUnexpected payload modification.\n")
    monkeypatch.setenv("FLOCKS_HUB_ROOT", str(bundle))
    catalog.clear_catalog_caches()

    with pytest.raises(ValueError, match="Checksum mismatch"):
        await install_plugin("skill", SKILL_ID)

    assert local.get_record("skill", SKILL_ID) is None
    assert not (cloudflare_env["home"] / ".flocks" / "plugins" / "skills" / SKILL_ID).exists()
    assert await Skill.get(SKILL_ID) is None
