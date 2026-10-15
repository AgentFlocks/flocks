"""How a non-text attachment reaches the model when it cannot be inlined."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from flocks.session.runner import SessionRunner


def _fallback(url: str, filename: str | None = "sample.bin", mime: str = "application/octet-stream") -> list[str]:
    runner = SimpleNamespace(
        provider_id="custom-fake",
        model_id="fake-model",
        _supports_multimodal_user_content=lambda: True,
    )
    blocks: list[dict] = []
    texts: list[str] = []
    SessionRunner._append_file_content_block(runner, blocks, texts, mime=mime, filename=filename, url=url)
    assert blocks == []
    return texts


@pytest.fixture
def deep_upload(tmp_path):
    """A binary upload under a long storage path (deep FLOCKS_ROOT, long session/upload ids)."""

    directory = tmp_path.joinpath(*["nested-flocks-root-directory"] * 6, "uploads", "ses_ec6393856ffecvj3g2P3vpssT6")
    directory.mkdir(parents=True)
    sample = directory / "part_ec6393856ffe0123456789abcd"
    sample.write_bytes(b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01")
    return sample


def test_a_binary_upload_keeps_its_whole_local_path(deep_upload):
    url = deep_upload.as_uri()
    assert len(url) > 200
    (text,) = _fallback(url, filename="rea_demo")
    assert text == f"[File: rea_demo]({url})"


def test_only_an_overlong_display_name_is_shortened(deep_upload):
    url = deep_upload.as_uri()
    (text,) = _fallback(url, filename="样本" * 100)
    assert text.endswith(f"]({url})")
    assert text.startswith("[File: " + "样本" * 60 + "…]")


def test_remote_urls_keep_the_hard_cap_and_data_urls_never_leak():
    (remote,) = _fallback("https://example.invalid/" + "a" * 500)
    assert len(remote) == 201 and remote.endswith("…")
    (inline,) = _fallback("data:application/octet-stream;base64," + "QUFB" * 200)
    assert inline == "[File: sample.bin]"


def test_an_attachment_without_a_filename_still_gets_its_path(deep_upload):
    url = deep_upload.as_uri()
    (text,) = _fallback(url, filename=None)
    assert text == f"[File: file]({url})"
