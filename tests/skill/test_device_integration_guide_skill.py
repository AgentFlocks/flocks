from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_FILE = (
    PROJECT_ROOT
    / ".flocks"
    / "plugins"
    / "skills"
    / "device-integration-guide"
    / "SKILL.md"
)


def test_device_integration_guide_queries_templates_before_custom_creation() -> None:
    content = SKILL_FILE.read_text(encoding="utf-8")

    assert 'device_manage(action="list_templates")' in content
    assert "设备实例为空不代表模板不存在" in content
    assert "没有匹配模板：进入自定义" in content


def test_device_integration_guide_routes_uninstalled_template_to_flockhub() -> None:
    content = SKILL_FILE.read_text(encoding="utf-8")

    assert "installed=false" in content
    assert "FlockHub 安装返回的 `plugin_id`" in content
    assert "不要创建自定义模板" in content


def test_device_integration_guide_creates_from_installed_template() -> None:
    content = SKILL_FILE.read_text(encoding="utf-8")

    assert 'device_manage(action="create")' in content
    assert 'device_manage(\n    action="create"' not in content
    assert "不能用 `update` 代替创建" in content
    assert "只有 `list_templates` 已返回匹配模板且 `installed=true`" in content
    assert "sensitive_fields_to_complete" in content
    assert "同一轮会话已经拿到成功返回的 `device_id`" in content


def test_device_integration_guide_does_not_embed_page_json_protocol() -> None:
    content = SKILL_FILE.read_text(encoding="utf-8")

    assert "```json" not in content
    assert '"storage_key":"<storage_key>"' not in content
    assert "一键回填" not in content
