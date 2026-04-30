import datetime as dt
import textwrap
from pathlib import Path

import pytest
from defusedxml.common import DefusedXmlException

from flocks.tool.registry import ToolContext, ToolRegistry
from flocks.workspace.manager import WorkspaceManager
from tests.utils.file_type_samples import (
    PARSER_SUPPORTED_FILENAMES,
    create_sample_file,
    expected_text_for_suffix,
)

def _load_module():
    import flocks.tool.file.doc_parser as module

    return module


def _replace_zip_entry(path: Path, entry_name: str, payload: str) -> None:
    from zipfile import ZIP_DEFLATED, ZipFile

    original_entries: dict[str, bytes] = {}
    with ZipFile(path) as archive:
        for info in archive.infolist():
            if info.filename == entry_name:
                continue
            original_entries[info.filename] = archive.read(info.filename)

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for filename, content in original_entries.items():
            archive.writestr(filename, content)
        archive.writestr(entry_name, payload)


@pytest.fixture(scope="module")
def doc_parser_module():
    module = _load_module()
    yield module
    ToolRegistry._tools.pop("doc_parser", None)


def test_default_output_path_uses_workspace_outputs(tmp_path, monkeypatch, doc_parser_module):
    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    try:
        output_path = doc_parser_module._default_output_path(Path("/tmp/合同 Final.docx"))
    finally:
        WorkspaceManager._instance = previous_instance

    expected_dir = tmp_path / "workspace" / "outputs" / dt.date.today().isoformat()
    assert output_path.parent == expected_dir
    assert output_path.name == "Final_docx.md"


def test_resolve_input_path_uses_workspace_dir_for_relative_paths(tmp_path, monkeypatch, doc_parser_module):
    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    workspace = tmp_path / "workspace"
    source = workspace / "uploads" / "report.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4")
    other_cwd = tmp_path / "service-cwd"
    other_cwd.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.chdir(other_cwd)
    try:
        resolved = doc_parser_module._resolve_input_path("uploads/report.pdf")
    finally:
        WorkspaceManager._instance = previous_instance

    assert resolved == source.resolve()


@pytest.mark.parametrize("filename", PARSER_SUPPORTED_FILENAMES)
@pytest.mark.asyncio
async def test_doc_parser_writes_real_file_markdown(tmp_path, doc_parser_module, filename):
    source = tmp_path / filename
    create_sample_file(source)

    output = tmp_path / f"{source.stem}.md"
    result = await doc_parser_module.doc_parser(
        ToolContext(session_id="test", message_id="test"),
        input_path=str(source),
        output_path=str(output),
    )

    assert result.success is True
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    assert expected_text_for_suffix(source.suffix) in content
    assert result.output["output_path"] == str(output)
    assert result.output["parser"]


@pytest.mark.asyncio
async def test_doc_parser_rejects_unsupported_file(tmp_path, doc_parser_module):
    source = tmp_path / "sample.txt"
    source.write_text("hello", encoding="utf-8")

    result = await doc_parser_module.doc_parser(
        ToolContext(session_id="test", message_id="test"),
        input_path=str(source),
    )

    assert result.success is False
    assert "Unsupported file type" in (result.error or "")


def test_normalize_markdown_preserves_fenced_code_block(doc_parser_module):
    normalized = doc_parser_module._normalize_markdown(
        "```python\n    print(1)\n\n    print(2)\n```\n"
    )

    assert "    print(1)" in normalized
    assert "    print(2)" in normalized


def test_docx_xml_fallback_preserves_soft_line_breaks(tmp_path, doc_parser_module):
    source = tmp_path / "soft-break.docx"
    create_sample_file(source)
    document = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p>
              <w:r><w:t>第一行</w:t></w:r>
              <w:r><w:br/></w:r>
              <w:r><w:t>第二行</w:t></w:r>
            </w:p>
          </w:body>
        </w:document>
    """)
    _replace_zip_entry(source, "word/document.xml", document)

    content = doc_parser_module._extract_docx_with_zipxml(source)

    assert "第一行\n第二行" in content


def test_docx_xml_fallback_rejects_xml_entities(tmp_path, doc_parser_module):
    source = tmp_path / "entity.docx"
    create_sample_file(source)
    document = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <!DOCTYPE w:document [
          <!ENTITY secret "blocked">
        ]>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p>
              <w:r><w:t>&secret;</w:t></w:r>
            </w:p>
          </w:body>
        </w:document>
    """)
    _replace_zip_entry(source, "word/document.xml", document)

    with pytest.raises(DefusedXmlException):
        doc_parser_module._extract_docx_with_zipxml(source)


def test_doc_fallback_prefers_pandoc_before_olefile(monkeypatch, tmp_path, doc_parser_module):
    source = tmp_path / "sample.doc"
    source.write_bytes(b"not-a-real-doc")

    calls: list[str] = []

    def fake_markitdown(_: Path) -> str:
        calls.append("markitdown")
        return ""

    def fake_pandoc(_: Path) -> str:
        calls.append("pandoc")
        return "converted"

    def fake_olefile(_: Path) -> str:
        calls.append("olefile")
        return "should-not-be-used"

    monkeypatch.setattr(doc_parser_module, "_extract_with_markitdown", fake_markitdown)
    monkeypatch.setattr(doc_parser_module, "_extract_with_pandoc", fake_pandoc)
    monkeypatch.setattr(doc_parser_module, "_extract_doc_with_olefile", fake_olefile)

    content, parser_name, errors = doc_parser_module._run_extractors(source)

    assert content == "converted"
    assert parser_name == "pandoc"
    assert errors == ["markitdown: extracted empty content"]
    assert calls == ["markitdown", "pandoc"]


@pytest.mark.parametrize(
    ("suffix", "replacement", "preferred_parser"),
    [
        (".ppt", "_extract_ppt_with_olefile", "olefile"),
        (".xls", "_extract_xls_with_olefile", "olefile"),
    ],
)
def test_legacy_office_fallback_uses_olefile_after_pandoc(
    monkeypatch,
    tmp_path,
    doc_parser_module,
    suffix,
    replacement,
    preferred_parser,
):
    source = tmp_path / f"legacy{suffix}"
    source.write_bytes(b"legacy-office")

    calls: list[str] = []

    def fake_markitdown(_: Path) -> str:
        calls.append("markitdown")
        return ""

    def fake_pandoc(_: Path) -> str:
        calls.append("pandoc")
        return ""

    def fake_ole(_: Path) -> str:
        calls.append("olefile")
        return "legacy text"

    monkeypatch.setattr(doc_parser_module, "_extract_with_markitdown", fake_markitdown)
    monkeypatch.setattr(doc_parser_module, "_extract_with_pandoc", fake_pandoc)
    monkeypatch.setattr(doc_parser_module, replacement, fake_ole)

    content, parser_name, errors = doc_parser_module._run_extractors(source)

    assert content == "legacy text"
    assert parser_name == preferred_parser
    assert errors == [
        "markitdown: extracted empty content",
        "pandoc: extracted empty content",
    ]
    assert calls == ["markitdown", "pandoc", "olefile"]
