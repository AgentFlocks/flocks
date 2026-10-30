import pytest
from types import SimpleNamespace

from flocks.contracts.webui import builder as builder_module
from flocks.contracts.webui.builder import WebUIPageBuilder, resolve_esbuild_bin
from flocks.contracts.webui.store import WebUIPagesStore


@pytest.mark.parametrize("os_name,machine,relative", [
    ("linux", "x86_64", "@esbuild/linux-x64/bin/esbuild"),
    ("linux", "aarch64", "@esbuild/linux-arm64/bin/esbuild"),
    ("darwin", "arm64", "@esbuild/darwin-arm64/bin/esbuild"),
    ("darwin", "x86_64", "@esbuild/darwin-x64/bin/esbuild"),
    ("win32", "AMD64", "@esbuild/win32-x64/esbuild.exe"),
    ("win32", "ARM64", "@esbuild/win32-arm64/esbuild.exe"),
])
def test_resolves_native_npm_compiler_without_a_launcher(tmp_path, monkeypatch, os_name, machine, relative):
    monkeypatch.setattr(builder_module, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(builder_module, "sys", SimpleNamespace(platform=os_name))
    monkeypatch.setattr(builder_module.platform, "machine", lambda: machine)
    executable = tmp_path / "webui/node_modules" / relative
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.chmod(0o755)
    # Windows' npm launcher can exist too; prefer the .exe, which needs no Node.
    if os_name == "win32":
        launcher = tmp_path / "webui/node_modules/.bin/esbuild.cmd"
        launcher.parent.mkdir()
        launcher.touch()
        launcher.chmod(0o755)
    assert resolve_esbuild_bin() == executable


@pytest.mark.parametrize("os_name,name", [("linux", "esbuild"), ("win32", "esbuild.exe")])
def test_resolves_packaged_standalone_compiler(tmp_path, monkeypatch, os_name, name):
    monkeypatch.setattr(builder_module, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(builder_module, "sys", SimpleNamespace(platform=os_name))
    executable = tmp_path / "webui/node_modules/.bin" / name
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.chmod(0o755)
    assert resolve_esbuild_bin() == executable


def test_missing_compiler_does_not_use_an_unrelated_path(tmp_path, monkeypatch):
    monkeypatch.setattr(builder_module, "_repo_root", lambda: tmp_path / "runtime")
    executable = tmp_path / "esbuild"
    executable.write_text("unrelated executable")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert resolve_esbuild_bin() is None


@pytest.fixture
def built_store(tmp_path, monkeypatch):
    root = tmp_path / "webui_pages"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    store = WebUIPagesStore()
    store.create_page(page_id="build-page", title="构建页")
    return store


@pytest.mark.skipif(resolve_esbuild_bin() is None, reason="esbuild is not installed")
def test_builder_produces_ready_bundle(built_store: WebUIPagesStore):
    builder = WebUIPageBuilder(built_store)
    meta = builder.build("build-page")
    assert meta.status == "ready"
    assert meta.hash
    assert built_store.bundle_path("build-page").is_file()


def test_builder_rejects_entry_outside_page_dir(built_store: WebUIPagesStore):
    built_store.create_page(page_id="build-page-neighbor", title="相邻页")
    built_store.save_manifest("build-page", {"entry": "../build-page-neighbor/src/index.tsx"})

    builder = WebUIPageBuilder(built_store)

    with pytest.raises(ValueError, match="invalid entry path"):
        builder.build("build-page")


@pytest.mark.skipif(resolve_esbuild_bin() is None, reason="esbuild is not installed")
def test_pages_can_import_the_visibility_and_event_hooks_from_the_sdk(built_store: WebUIPagesStore):
    source = built_store.page_dir("build-page") / "src" / "index.tsx"
    source.write_text(
        "import { usePaneActive, useServerEvents } from '@flocks/webui-contract-sdk';\n"
        "export default function Page() {\n"
        "  const active = usePaneActive ? usePaneActive() : true;\n"
        "  if (useServerEvents) useServerEvents(() => undefined, { enabled: active });\n"
        "  return <div>{String(active)}</div>;\n"
        "}\n",
        encoding="utf-8",
    )
    meta = WebUIPageBuilder(built_store).build("build-page")
    assert meta.status == "ready", meta.error

