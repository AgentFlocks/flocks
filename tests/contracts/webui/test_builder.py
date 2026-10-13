import pytest

from flocks.contracts.webui.builder import WebUIPageBuilder, resolve_esbuild_bin
from flocks.contracts.webui.store import WebUIPagesStore


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

