import datetime as dt
import textwrap
import zipfile
from pathlib import Path

import pytest
from defusedxml.common import DefusedXmlException

from flocks.tool.registry import ToolContext, ToolRegistry
from flocks.workspace.manager import WorkspaceManager

def _load_module():
    import flocks.tool.file.doc_parser as module

    return module


def _write_minimal_docx(path: Path, document_xml: str | None = None) -> None:
    content_types = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
          <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
        </Types>
    """)
    rels = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
        </Relationships>
    """)
    styles = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:style w:type="paragraph" w:styleId="Heading1">
            <w:name w:val="heading 1"/>
          </w:style>
          <w:style w:type="paragraph" w:styleId="Normal">
            <w:name w:val="Normal"/>
          </w:style>
        </w:styles>
    """)
    document = document_xml or textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p>
              <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
              <w:r><w:t>合同标题</w:t></w:r>
            </w:p>
            <w:p>
              <w:r><w:t>第一段内容</w:t></w:r>
            </w:p>
          </w:body>
        </w:document>
    """)

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)


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


@pytest.mark.asyncio
async def test_doc_parser_writes_docx_markdown(tmp_path, doc_parser_module):
    source = tmp_path / "sample.docx"
    _write_minimal_docx(source)

    output = tmp_path / "sample.md"
    result = await doc_parser_module.doc_parser(
        ToolContext(session_id="test", message_id="test"),
        input_path=str(source),
        output_path=str(output),
    )

    assert result.success is True
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    assert "# 合同标题" in content
    assert "第一段内容" in content
    assert result.output["output_path"] == str(output)
    assert result.output["parser"] in {"markitdown", "docx-xml", "pandoc"}


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
    _write_minimal_docx(source, document_xml=document)

    content = doc_parser_module._extract_docx_with_zipxml(source)

    assert "第一行\n第二行" in content


def test_docx_xml_fallback_rejects_xml_entities(tmp_path, doc_parser_module):
    source = tmp_path / "entity.docx"
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
    _write_minimal_docx(source, document_xml=document)

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
