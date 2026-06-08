import pytest

from flocks.user_defined_pages.builder import UserDefinedPagesBuilder, resolve_esbuild_bin
from flocks.user_defined_pages.store import UserDefinedPagesStore


@pytest.fixture
def built_store(tmp_path, monkeypatch):
    root = tmp_path / "user_defined_pages"
    monkeypatch.setenv("FLOCKS_USER_DEFINED_PAGES_ROOT", str(root))
    store = UserDefinedPagesStore()
    store.create_page(page_id="build-page", title="构建页")
    return store


@pytest.mark.skipif(resolve_esbuild_bin() is None, reason="esbuild is not installed")
def test_builder_produces_ready_bundle(built_store: UserDefinedPagesStore):
    builder = UserDefinedPagesBuilder(built_store)
    meta = builder.build("build-page")
    assert meta.status == "ready"
    assert meta.hash
    assert built_store.bundle_path("build-page").is_file()


def test_builder_rejects_entry_outside_page_dir(built_store: UserDefinedPagesStore):
    built_store.create_page(page_id="build-page-neighbor", title="相邻页")
    built_store.save_manifest("build-page", {"entry": "../build-page-neighbor/src/index.tsx"})

    builder = UserDefinedPagesBuilder(built_store)

    with pytest.raises(ValueError, match="invalid entry path"):
        builder.build("build-page")
