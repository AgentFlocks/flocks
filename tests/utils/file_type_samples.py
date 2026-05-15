from __future__ import annotations

import shutil
import textwrap
import zipfile
from pathlib import Path


FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "file_types"

ALL_SUPPORTED_UPLOAD_FILENAMES = [
    "sample.txt",
    "sample.md",
    "sample.json",
    "sample.yaml",
    "sample.yml",
    "sample.xml",
    "sample.csv",
    "sample.pdf",
    "sample.doc",
    "sample.docx",
    "sample.html",
    "sample.htm",
    "sample.ppt",
    "sample.pptx",
    "sample.xls",
    "sample.xlsx",
]

PARSER_SUPPORTED_FILENAMES = [
    "sample.pdf",
    "sample.doc",
    "sample.docx",
    "sample.html",
    "sample.htm",
    "sample.ppt",
    "sample.pptx",
    "sample.xls",
    "sample.xlsx",
]

_TEXT_CONTENT_BY_SUFFIX = {
    ".txt": "Plain text sample\nSecond line\n",
    ".md": "# Markdown sample\n\n- bullet item\n",
    ".json": '{\n  "message": "json sample"\n}\n',
    ".yaml": "message: yaml sample\ncount: 1\n",
    ".yml": "message: yml sample\ncount: 1\n",
    ".xml": "<root><message>xml sample</message></root>\n",
    ".csv": "name,score\nAlice,95\n",
    ".html": "<html><body><h1>报告标题</h1><p>正文内容</p><ul><li>条目A</li></ul></body></html>\n",
    ".htm": "<html><body><h1>简版标题</h1><p>HTM 内容</p></body></html>\n",
}

_EXPECTED_TEXT_BY_SUFFIX = {
    ".pdf": "PDF sample text",
    ".doc": "Legacy office sample",
    ".docx": "合同标题",
    ".html": "报告标题",
    ".htm": "简版标题",
    ".ppt": "Legacy office sample",
    ".pptx": "季度汇报",
    ".xls": "Legacy office sample",
    ".xlsx": "Alice",
}

_LEGACY_FIXTURE_BY_SUFFIX = {
    ".doc": FIXTURE_DIR / "sample.doc",
    ".ppt": FIXTURE_DIR / "sample.ppt",
    ".xls": FIXTURE_DIR / "sample.xls",
}


def expected_text_for_suffix(suffix: str) -> str:
    return _EXPECTED_TEXT_BY_SUFFIX[suffix.lower()]


def create_sample_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _TEXT_CONTENT_BY_SUFFIX:
        path.write_text(_TEXT_CONTENT_BY_SUFFIX[suffix], encoding="utf-8")
        return _TEXT_CONTENT_BY_SUFFIX[suffix]
    if suffix == ".pdf":
        _write_pdf(path)
        return _EXPECTED_TEXT_BY_SUFFIX[suffix]
    if suffix == ".docx":
        _write_docx(path)
        return _EXPECTED_TEXT_BY_SUFFIX[suffix]
    if suffix == ".pptx":
        _write_pptx(path)
        return _EXPECTED_TEXT_BY_SUFFIX[suffix]
    if suffix == ".xlsx":
        _write_xlsx(path)
        return _EXPECTED_TEXT_BY_SUFFIX[suffix]
    if suffix in _LEGACY_FIXTURE_BY_SUFFIX:
        fixture = _LEGACY_FIXTURE_BY_SUFFIX[suffix]
        if not fixture.exists():
            raise FileNotFoundError(f"Missing legacy fixture: {fixture}")
        shutil.copyfile(fixture, path)
        return _EXPECTED_TEXT_BY_SUFFIX[suffix]
    raise ValueError(f"Unsupported sample suffix: {suffix}")


def _write_pdf(path: Path) -> None:
    import fitz

    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), _EXPECTED_TEXT_BY_SUFFIX[".pdf"])
        document.save(path)
    finally:
        document.close()


def _write_docx(path: Path) -> None:
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
    document = textwrap.dedent("""\
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


def _write_pptx(path: Path) -> None:
    slide = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
               xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
          <p:cSld>
            <p:spTree>
              <p:sp>
                <p:txBody>
                  <a:p><a:r><a:t>季度汇报</a:t></a:r></a:p>
                  <a:p><a:r><a:t>收入增长 20%</a:t></a:r></a:p>
                </p:txBody>
              </p:sp>
            </p:spTree>
          </p:cSld>
        </p:sld>
    """)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)


def _write_xlsx(path: Path) -> None:
    workbook = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <sheets>
            <sheet name="Sheet1" sheetId="1" r:id="rId1"
                   xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>
          </sheets>
        </workbook>
    """)
    shared_strings = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">
          <si><t>姓名</t></si>
          <si><t>Alice</t></si>
        </sst>
    """)
    sheet = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <sheetData>
            <row r="1">
              <c r="A1" t="s"><v>0</v></c>
              <c r="B1" t="inlineStr"><is><t>分数</t></is></c>
            </row>
            <row r="2">
              <c r="A2" t="s"><v>1</v></c>
              <c r="B2"><v>95</v></c>
            </row>
          </sheetData>
        </worksheet>
    """)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/sharedStrings.xml", shared_strings)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
