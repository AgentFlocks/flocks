"""flocks.jsonc: comments *and* trailing commas (what VS Code writes) must parse."""
from __future__ import annotations

from pathlib import Path

import pytest

from flocks.config.config import Config


def test_parse_jsonc_accepts_trailing_commas_and_comments():
    text = """{
      // line comment
      "theme": "jsonc-theme", /* block */
      "provider": {
        "x": {"models": {"m": {"name": "m",},},},
      },
      "list": [1, 2, 3,],
    }"""
    data = Config.parse_jsonc(text, Path("flocks.jsonc"))
    assert data["theme"] == "jsonc-theme"
    assert data["provider"]["x"]["models"]["m"]["name"] == "m"
    assert data["list"] == [1, 2, 3]


def test_parse_jsonc_keeps_commas_inside_strings_and_rejects_bad_json():
    data = Config.parse_jsonc('{"a": "x,}", "b": "//not-a-comment",}', Path("f.jsonc"))
    assert data == {"a": "x,}", "b": "//not-a-comment"}
    with pytest.raises(ValueError, match="Invalid JSON"):
        Config.parse_jsonc('{"a": ,}', Path("f.jsonc"))
    with pytest.raises(ValueError, match="unterminated block comment"):
        Config.parse_jsonc('{"a": 1 /* open', Path("f.jsonc"))
