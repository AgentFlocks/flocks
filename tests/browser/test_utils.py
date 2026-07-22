import os

from flocks.browser import utils


def test_read_env_text_supports_utf8(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=中文\n", encoding="utf-8")

    assert utils.read_env_text(env_file) == "TOKEN=中文\n"


def test_load_env_file_supports_utf8_bom(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes('TOKEN="中文"\nNAME=test\n'.encode("utf-8-sig"))
    monkeypatch.delenv("TOKEN", raising=False)
    monkeypatch.delenv("NAME", raising=False)

    utils.load_env_file(env_file)

    assert os.environ["TOKEN"] == "中文"
    assert os.environ["NAME"] == "test"


def test_read_env_text_falls_back_to_local_encoding(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes("TOKEN=中文\n".encode("gbk"))
    monkeypatch.setattr(utils.locale, "getpreferredencoding", lambda _do_setlocale=False: "gbk")

    assert utils.read_env_text(env_file) == "TOKEN=中文\n"
