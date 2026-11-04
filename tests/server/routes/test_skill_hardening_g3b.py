"""G3b 技能路由加固回归测试（TOL-1061/1062/1063/1064/1065/1066/1077/1079/1076/1083）。

覆盖本轮修复：
  - POST /api/skills name 路径穿越 / 绝对路径 / 非法字符 → 400，空名 → 422，且不落盘
  - 同名已存在 → 409；重命名到已存在名 → 409
  - description 含换行 → 400（frontmatter 注入）
  - 非用户目录（内置）技能同名更新 / 删除 → 403
"""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from flocks.server.routes import skill as skill_routes
from flocks.skill.skill import Skill


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(skill_routes, "_user_skills_root", lambda: root)
    Skill.clear_cache()
    yield root
    Skill.clear_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name", ["../../pwn", "/tmp/abs", "My Skill", "a/b", "..", "-lead"])
async def test_create_rejects_unsafe_names(client: AsyncClient, skills_root: Path, tmp_path: Path, bad_name: str):
    resp = await client.post("/api/skills", json={"name": bad_name, "description": "d", "content": "c"})
    assert resp.status_code == 400, resp.text
    assert "Invalid skill name" in resp.json()["message"]
    # 不落盘：技能根目录仍为空，且没有写到 tmp_path 之外的位置
    assert list(skills_root.iterdir()) == []
    assert not (tmp_path / "pwn").exists() and not Path("/tmp/abs/SKILL.md").exists()


@pytest.mark.asyncio
async def test_create_rejects_empty_name(client: AsyncClient, skills_root: Path):
    resp = await client.post("/api/skills", json={"name": "", "description": "d", "content": "c"})
    assert resp.status_code == 422, resp.text
    assert not (skills_root / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_create_rejects_multiline_description(client: AsyncClient, skills_root: Path):
    resp = await client.post("/api/skills", json={"name": "nl-skill", "description": "a\nname: x", "content": "c"})
    assert resp.status_code == 400, resp.text
    assert "single line" in resp.json()["message"]
    assert not (skills_root / "nl-skill").exists()


@pytest.mark.asyncio
async def test_create_conflict_409(client: AsyncClient, skills_root: Path):
    ok = await client.post("/api/skills", json={"name": "dup-skill", "description": "d", "content": "one"})
    assert ok.status_code == 201, ok.text
    dup = await client.post("/api/skills", json={"name": "dup-skill", "description": "d", "content": "two"})
    assert dup.status_code == 409, dup.text
    assert "one" in (skills_root / "dup-skill" / "SKILL.md").read_text()


@pytest.mark.asyncio
async def test_rename_rejects_traversal_and_collision(client: AsyncClient, skills_root: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("ren-a", "ren-b"):
        r = await client.post("/api/skills", json={"name": name, "description": "d", "content": "c"})
        assert r.status_code == 201, r.text
    Skill.clear_cache()

    # 让 Skill.get 找到刚创建的用户技能（发现路径依赖 ~/.flocks，此处直接 stub）
    from flocks.skill.skill import SkillInfo

    async def fake_get(name: str):
        p = skills_root / name / "SKILL.md"
        if not p.exists():
            return None
        return SkillInfo(name=name, description="d", location=str(p), source="user")

    monkeypatch.setattr(Skill, "get", fake_get)

    trav = await client.put("/api/skills/ren-a", json={"name": "../../x", "description": "d", "content": "c"})
    assert trav.status_code == 400, trav.text
    assert (skills_root / "ren-a" / "SKILL.md").exists()

    collide = await client.put("/api/skills/ren-a", json={"name": "ren-b", "description": "d", "content": "c"})
    assert collide.status_code == 409, collide.text
    assert (skills_root / "ren-a" / "SKILL.md").exists() and (skills_root / "ren-b" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_builtin_skill_update_and_delete_forbidden(client: AsyncClient, skills_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from flocks.skill.skill import SkillInfo

    builtin_dir = tmp_path / "src" / ".flocks" / "skills" / "core-skill"
    builtin_dir.mkdir(parents=True)
    builtin_path = builtin_dir / "SKILL.md"
    builtin_path.write_text("---\nname: core-skill\ndescription: d\n---\n\nbuiltin", encoding="utf-8")

    async def fake_get(name: str):
        if name == "core-skill":
            return SkillInfo(name=name, description="d", location=str(builtin_path), source="flocks")
        return None

    monkeypatch.setattr(Skill, "get", fake_get)

    upd = await client.put("/api/skills/core-skill", json={"name": "core-skill", "description": "d2", "content": "hacked"})
    assert upd.status_code == 403, upd.text
    assert "builtin" in builtin_path.read_text()

    dele = await client.delete("/api/skills/core-skill")
    assert dele.status_code == 403, dele.text
    assert builtin_path.exists()


# ---------------------------------------------------------------- 安装接口加固（TOL-1037/1038/1040/1043/1054/1057）

@pytest.mark.asyncio
async def test_install_scope_validated_and_admin_only(client: AsyncClient):
    """scope 非法 → 422；install 与 install-deps 声明 require_admin。"""
    from flocks.server import auth as auth_module

    resp = await client.post("/api/skills/install", json={"source": "/tmp/nope", "scope": "team"})
    assert resp.status_code == 422, resp.text
    guarded = {"/skills/install", "/skills/{name}/install-deps"}
    seen = set()
    for route in skill_routes.router.routes:
        if getattr(route, "path", None) in guarded and "POST" in getattr(route, "methods", set()):
            seen.add(route.path)
            assert auth_module.require_admin in [d.call for d in route.dependant.dependencies], route.path
    assert seen == guarded


@pytest.mark.asyncio
async def test_install_deps_timeout_must_be_positive(client: AsyncClient):
    for value in (0, -5):
        resp = await client.post("/api/skills/any/install-deps", json={"timeout_ms": value})
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_local_install_conflict_409_unless_overwrite(client: AsyncClient, skills_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from flocks.skill import installer as installer_mod

    monkeypatch.setattr(installer_mod, "_resolve_install_root", lambda scope: skills_root)
    src = tmp_path / "sk"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: dup-inst\ndescription: d\n---\n\none", encoding="utf-8")
    first = await client.post("/api/skills/install", json={"source": str(src)})
    assert first.status_code == 200, first.text
    (src / "SKILL.md").write_text("---\nname: dup-inst\ndescription: d\n---\n\ntwo", encoding="utf-8")
    again = await client.post("/api/skills/install", json={"source": str(src)})
    assert again.status_code == 409, again.text
    assert "one" in (skills_root / "dup-inst" / "SKILL.md").read_text()
    forced = await client.post("/api/skills/install", json={"source": str(src), "overwrite": True})
    assert forced.status_code == 200, forced.text
    assert "two" in (skills_root / "dup-inst" / "SKILL.md").read_text()


@pytest.mark.asyncio
async def test_url_install_rejects_plain_http_and_internal(monkeypatch: pytest.MonkeyPatch):
    from flocks.skill.installer import SkillInstaller

    monkeypatch.delenv("FLOCKS_WEBFETCH_ALLOW_INTERNAL", raising=False)
    r1 = await SkillInstaller._install_from_url("http://example.com/SKILL.md", "global")
    assert r1.success is False and "must use https" in r1.error
    r2 = await SkillInstaller._install_from_url("https://169.254.169.254/latest/meta-data/", "global")
    assert r2.success is False and "Blocked request to internal" in r2.error


@pytest.mark.asyncio
async def test_install_clears_stale_disabled_flag(client: AsyncClient, skills_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from flocks.skill import installer as installer_mod

    monkeypatch.setattr(installer_mod, "_resolve_install_root", lambda scope: skills_root)
    calls = []
    monkeypatch.setattr(Skill, "forget_disabled", classmethod(lambda cls, name: calls.append(name)))
    src = tmp_path / "sk2"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: fresh-inst\ndescription: d\n---\n\nx", encoding="utf-8")
    resp = await client.post("/api/skills/install", json={"source": str(src)})
    assert resp.status_code == 200, resp.text
    assert calls == ["fresh-inst"]
