from flocks.browser import daemon


def test_is_real_page_filters_edge_internal_pages() -> None:
    assert not daemon.is_real_page({"type": "page", "url": "edge://inspect/#remote-debugging"})


def test_is_real_page_accepts_normal_https_pages() -> None:
    assert daemon.is_real_page({"type": "page", "url": "https://example.com"})


def test_load_env_uses_shared_loader_for_existing_files(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo_env = repo_root / ".env"
    workspace_env = workspace / ".env"
    repo_env.write_text("TOKEN=repo\n", encoding="utf-8")
    workspace_env.write_text("TOKEN=workspace\n", encoding="utf-8")
    loaded_paths = []

    class _FakeModulePath:
        def resolve(self):
            return self

        @property
        def parents(self):
            return [None, None, repo_root]

    monkeypatch.setattr(daemon, "AGENT_WORKSPACE", workspace)
    monkeypatch.setattr(daemon, "Path", lambda _value: _FakeModulePath())
    monkeypatch.setattr(daemon, "load_env_file", lambda path: loaded_paths.append(path))

    daemon._load_env()

    assert loaded_paths == [repo_env, workspace_env]
