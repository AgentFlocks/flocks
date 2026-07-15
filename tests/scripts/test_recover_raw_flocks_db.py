"""Safety tests for the offline SQLite recovery helper."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "recover_raw_flocks_db.py"
    module_name = "recover_raw_flocks_db_test_module"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


recover_raw_flocks_db = load_module()


def _require_sqlite_recover() -> None:
    sqlite_bin = shutil.which("sqlite3")
    if sqlite_bin is None:
        pytest.skip("sqlite3 CLI is required for the recovery integration test")
    supported, reason = recover_raw_flocks_db._sqlite_recover_capability(sqlite_bin)
    if not supported:
        pytest.skip(f"sqlite3 .recover is unavailable: {reason}")


def _wal_checksum(data, *, byte_order, state=(0, 0)):
    assert len(data) % 8 == 0
    words = struct.unpack(f"{byte_order}{len(data) // 4}I", data)
    s0, s1 = state
    for index in range(0, len(words), 2):
        s0 = (s0 + words[index] + s1) & 0xFFFFFFFF
        s1 = (s1 + words[index + 1] + s0) & 0xFFFFFFFF
    return s0, s1


def _build_wal(frames, *, page_size=1024, magic=None):
    magic = magic or recover_raw_flocks_db.WAL_MAGIC
    byte_order = "<" if magic == 0x377F0682 else ">"
    salt1, salt2 = 0x12345678, 0x90ABCDEF
    header_prefix = struct.pack(
        ">6I",
        magic,
        recover_raw_flocks_db.WAL_VERSION,
        page_size,
        0,
        salt1,
        salt2,
    )
    checksum = _wal_checksum(header_prefix, byte_order=byte_order)
    wal = bytearray(header_prefix + struct.pack(">2I", *checksum))
    for page_no, db_page_count, fill_byte in frames:
        page = bytes([fill_byte]) * page_size
        frame_prefix = struct.pack(">4I", page_no, db_page_count, salt1, salt2)
        checksum = _wal_checksum(frame_prefix[:8], byte_order=byte_order, state=checksum)
        checksum = _wal_checksum(page, byte_order=byte_order, state=checksum)
        wal.extend(frame_prefix + struct.pack(">2I", *checksum) + page)
    return bytes(wal)


def test_default_artifacts_dir_is_unique_and_not_created(tmp_path, monkeypatch):
    workspace_dir = tmp_path / "workspace"
    raw_path = tmp_path / "damaged.db"
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace_dir))

    first = recover_raw_flocks_db._default_artifacts_dir(raw_path)
    second = recover_raw_flocks_db._default_artifacts_dir(raw_path)

    assert first != second
    assert first.parent == second.parent
    assert first.exists() is False
    assert second.exists() is False


def test_empty_data_dir_env_matches_runtime_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLOCKS_DATA_DIR", "")

    assert recover_raw_flocks_db._resolve_live_data_dir() == tmp_path.resolve()


def test_lowercase_data_dir_env_matches_runtime(tmp_path, monkeypatch):
    data_dir = tmp_path / "lowercase-live-data"
    monkeypatch.delenv("FLOCKS_DATA_DIR", raising=False)
    monkeypatch.setenv("flocks_data_dir", str(data_dir))

    assert recover_raw_flocks_db._resolve_live_data_dir() == data_dir.resolve()


@pytest.mark.skipif(os.name == "nt", reason="Windows environment keys are case-insensitive")
def test_data_dir_case_variant_priority_matches_runtime(tmp_path, monkeypatch):
    from flocks.config.config import GlobalConfig

    uppercase_dir = tmp_path / "uppercase"
    lowercase_dir = tmp_path / "lowercase"
    monkeypatch.delenv("FLOCKS_DATA_DIR", raising=False)
    monkeypatch.delenv("flocks_data_dir", raising=False)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(uppercase_dir))
    monkeypatch.setenv("flocks_data_dir", str(lowercase_dir))

    assert recover_raw_flocks_db._resolve_live_data_dir() == GlobalConfig().data_dir.resolve()


def test_cli_loads_dotenv_before_protecting_live_data_dir(tmp_path, monkeypatch):
    live_data = tmp_path / "configured-live-data"
    live_data.mkdir()
    live_db = live_data / "flocks.db"
    live_db.write_bytes(b"live")
    (tmp_path / ".env").write_text(
        f"FLOCKS_DATA_DIR={live_data}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLOCKS_DATA_DIR", raising=False)

    with pytest.raises(ValueError, match="live Flocks database"):
        recover_raw_flocks_db.main([str(live_db)])


def test_missing_input_does_not_touch_live_sidecars(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    wal_path = data_dir / "flocks.db-wal"
    shm_path = data_dir / "flocks.db-shm"
    wal_path.write_bytes(b"live-wal")
    shm_path.write_bytes(b"live-shm")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    with pytest.raises(FileNotFoundError):
        recover_raw_flocks_db.main([str(tmp_path / "missing.db")])

    assert wal_path.read_bytes() == b"live-wal"
    assert shm_path.read_bytes() == b"live-shm"


def test_recovery_cli_keeps_live_wal_usable(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    db_path = data_dir / "flocks.db"
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    holder = sqlite3.connect(db_path)
    try:
        assert holder.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        holder.execute("CREATE TABLE live_rows(value INTEGER)")
        holder.execute("INSERT INTO live_rows VALUES (1)")
        holder.commit()
        assert db_path.with_name("flocks.db-wal").exists()
        assert db_path.with_name("flocks.db-shm").exists()

        with pytest.raises(FileNotFoundError):
            recover_raw_flocks_db.main([str(tmp_path / "missing.db")])

        holder.execute("INSERT INTO live_rows VALUES (2)")
        holder.commit()
        with sqlite3.connect(db_path) as probe:
            assert probe.execute("SELECT value FROM live_rows ORDER BY value").fetchall() == [
                (1,),
                (2,),
            ]
    finally:
        holder.close()


def test_rejects_live_database_as_output_before_recovery(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    live_db = data_dir / "flocks.db"
    live_db.write_bytes(b"live-db")
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    recovery_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal recovery_called
        recovery_called = True
        raise AssertionError("recovery must not start for an unsafe output path")

    monkeypatch.setattr(recover_raw_flocks_db, "recover_raw_storage_db", fail_if_called)

    with pytest.raises(ValueError, match="live Flocks database"):
        recover_raw_flocks_db.main([str(damaged_db), "--output", str(live_db)])

    assert recovery_called is False
    assert live_db.read_bytes() == b"live-db"


def test_rejects_live_database_as_input_before_writing_artifacts(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    live_db = data_dir / "flocks.db"
    live_db.write_bytes(b"live-db")
    workspace_dir = tmp_path / "workspace"
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace_dir))

    with pytest.raises(ValueError, match="recover the live Flocks database"):
        recover_raw_flocks_db.main([str(live_db)])

    assert live_db.read_bytes() == b"live-db"
    assert workspace_dir.exists() is False


def test_rejects_artifact_plan_that_writes_live_database(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    live_db = data_dir / "flocks.db"
    live_db.write_bytes(b"live-db")
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    recovery_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal recovery_called
        recovery_called = True
        raise AssertionError("recovery must not start for an unsafe artifact path")

    monkeypatch.setattr(recover_raw_flocks_db, "recover_raw_storage_db", fail_if_called)

    with pytest.raises(ValueError, match="live Flocks database"):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--artifacts-dir",
                str(data_dir),
                "--prefix",
                "flocks",
            ]
        )

    assert recovery_called is False
    assert live_db.read_bytes() == b"live-db"


@pytest.mark.parametrize("prefix, live_name", [("tasks", "tasks.db"), ("workflow", "workflow.db")])
def test_rejects_artifacts_anywhere_in_live_data_dir(
    tmp_path,
    monkeypatch,
    prefix,
    live_name,
):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    live_db = data_dir / live_name
    live_db.write_bytes(b"live-db")
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    monkeypatch.setattr(
        recover_raw_flocks_db,
        "recover_raw_storage_db",
        lambda *_args, **_kwargs: pytest.fail("unsafe recovery plan must be rejected"),
    )

    with pytest.raises(ValueError, match="live Flocks data directory"):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--artifacts-dir",
                str(data_dir),
                "--prefix",
                prefix,
            ]
        )

    assert live_db.read_bytes() == b"live-db"


def test_rejects_existing_artifact_before_recovery(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    candidate_db = artifacts_dir / "safe.candidate.db"
    candidate_db.write_bytes(b"existing-evidence")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    with pytest.raises(FileExistsError, match="existing recovery file"):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--artifacts-dir",
                str(artifacts_dir),
                "--prefix",
                "safe",
            ]
        )

    assert candidate_db.read_bytes() == b"existing-evidence"


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_rejects_existing_sqlite_sidecar_before_recovery(tmp_path, monkeypatch, suffix):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    sidecar_path = artifacts_dir / f"safe.db{suffix}"
    sidecar_path.write_bytes(b"existing-sidecar")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    with pytest.raises(FileExistsError, match="existing recovery file"):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--artifacts-dir",
                str(artifacts_dir),
                "--prefix",
                "safe",
            ]
        )

    assert sidecar_path.read_bytes() == b"existing-sidecar"


def test_rejects_hardlinked_artifact_to_live_database(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    live_db = data_dir / "flocks.db"
    live_db.write_bytes(b"live-db")
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    os.link(live_db, artifacts_dir / "safe.candidate.db")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    with pytest.raises(ValueError, match="live Flocks database"):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--artifacts-dir",
                str(artifacts_dir),
                "--prefix",
                "safe",
            ]
        )

    assert live_db.read_bytes() == b"live-db"


def test_direct_recovery_call_rejects_live_data_directory(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    live_db = data_dir / "flocks.db"
    live_db.write_bytes(b"live-db")
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    with pytest.raises(ValueError, match="live Flocks database"):
        recover_raw_flocks_db.recover_raw_storage_db(
            damaged_db,
            None,
            data_dir,
            prefix="flocks",
        )

    assert live_db.read_bytes() == b"live-db"


def test_rejects_output_collision_with_intermediate_artifact(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    artifacts_dir = tmp_path / "artifacts"
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    with pytest.raises(ValueError, match="collides with an intermediate artifact"):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--output",
                str(artifacts_dir / "safe.summary.txt"),
                "--artifacts-dir",
                str(artifacts_dir),
                "--prefix",
                "safe",
            ]
        )

    assert artifacts_dir.exists() is False


def test_rejects_output_collision_with_sqlite_sidecar(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    artifacts_dir = tmp_path / "artifacts"
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    with pytest.raises(ValueError, match="collides with an intermediate artifact"):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--output",
                str(artifacts_dir / "safe.db-journal"),
                "--artifacts-dir",
                str(artifacts_dir),
                "--prefix",
                "safe",
            ]
        )

    assert artifacts_dir.exists() is False


def test_rejects_case_insensitive_output_collision(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    artifacts_dir = tmp_path / "artifacts"
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    with pytest.raises(ValueError, match="ambiguous case-insensitive collision"):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--output",
                str(artifacts_dir / "SAFE.db"),
                "--artifacts-dir",
                str(artifacts_dir),
                "--prefix",
                "safe",
            ]
        )

    assert artifacts_dir.exists() is False


def test_rejects_wal_path_that_collides_with_artifact(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    wal_path = artifacts_dir / "safe.candidate.db"
    wal_path.write_bytes(b"wal-evidence")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    with pytest.raises(ValueError, match="source evidence"):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--wal",
                str(wal_path),
                "--artifacts-dir",
                str(artifacts_dir),
                "--prefix",
                "safe",
            ]
        )

    assert wal_path.read_bytes() == b"wal-evidence"


def test_rejects_live_wal_as_recovery_input(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    live_wal = data_dir / "flocks.db-wal"
    live_wal.write_bytes(b"live-wal")
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    with pytest.raises(ValueError, match="live Flocks SQLite sidecar"):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--wal",
                str(live_wal),
                "--artifacts-dir",
                str(tmp_path / "artifacts"),
            ]
        )

    assert live_wal.read_bytes() == b"live-wal"


def test_detects_storage_quarantine_wal_name(tmp_path):
    raw_path = tmp_path / "flocks.db.corrupt.20260710T083229"
    wal_path = tmp_path / "flocks.db-wal.corrupt.20260710T083229"
    raw_path.write_bytes(b"raw")
    wal_path.write_bytes(b"wal")

    assert recover_raw_flocks_db._detect_wal_path(raw_path) == wal_path.resolve()


def test_auto_detect_ignores_empty_checkpointed_wal(tmp_path):
    raw_path = tmp_path / "flocks.db"
    raw_path.write_bytes(b"main")
    raw_path.with_name("flocks.db-wal").touch()

    assert recover_raw_flocks_db._detect_wal_path(raw_path) is None


@pytest.mark.parametrize("magic", [0x377F0682, 0x377F0683])
def test_wal_parser_discards_frames_after_last_commit(magic):
    wal_bytes = _build_wal(
        [
            (1, 1, ord("A")),
            (1, 0, ord("B")),
        ],
        magic=magic,
    )

    page_size, final_pages, latest_pages, frame_count = recover_raw_flocks_db._parse_wal_frames(
        wal_bytes
    )

    assert page_size == 1024
    assert final_pages == 1
    assert frame_count == 1
    assert latest_pages[1] == b"A" * 1024


def test_wal_parser_rejects_invalid_header_checksum():
    wal_bytes = bytearray(_build_wal([(1, 1, ord("A"))]))
    wal_bytes[24] ^= 0xFF

    with pytest.raises(ValueError, match="header checksum"):
        recover_raw_flocks_db._parse_wal_frames(bytes(wal_bytes))


def test_real_wal_snapshot_does_not_replay_uncommitted_spill(tmp_path):
    live_db = tmp_path / "live.db"
    source = sqlite3.connect(live_db)
    try:
        assert source.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute("PRAGMA cache_size=5")
        source.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, value BLOB)")
        source.executemany(
            "INSERT INTO records(value) VALUES (?)",
            [(b"a" * 3500,) for _ in range(300)],
        )
        source.commit()

        source.execute("BEGIN IMMEDIATE")
        source.executemany(
            "UPDATE records SET value=? WHERE id=?",
            [(b"b" * 3500, row_id) for row_id in range(1, 301)],
        )

        raw_copy = tmp_path / "offline.db"
        wal_copy = tmp_path / "offline.db-wal"
        shutil.copy2(live_db, raw_copy)
        shutil.copy2(live_db.with_name(f"{live_db.name}-wal"), wal_copy)
    finally:
        source.rollback()
        source.close()

    wal_bytes = wal_copy.read_bytes()
    page_size = int.from_bytes(wal_bytes[8:12], "big")
    physical_frames = (len(wal_bytes) - 32) // (24 + page_size)
    candidate_db = tmp_path / "candidate.db"
    stats = recover_raw_flocks_db.reconstruct_sqlite_candidate(
        raw_copy,
        wal_copy,
        candidate_db,
    )

    assert stats["wal_frames"] < physical_frames
    with sqlite3.connect(candidate_db) as db:
        assert db.execute("SELECT DISTINCT substr(value, 1, 1) FROM records").fetchall() == [
            (b"a",)
        ]


def test_reconstruct_uses_valid_512_byte_header_page_size(tmp_path):
    source_db = tmp_path / "page-512.db"
    with sqlite3.connect(source_db) as db:
        db.execute("PRAGMA page_size=512")
        db.execute("VACUUM")
        db.execute("CREATE TABLE probe(value TEXT)")
        db.execute("INSERT INTO probe VALUES ('ok')")
        db.commit()
        assert db.execute("PRAGMA page_size").fetchone()[0] == 512

    candidate_db = tmp_path / "candidate.db"
    stats = recover_raw_flocks_db.reconstruct_sqlite_candidate(source_db, None, candidate_db)

    assert stats["pagesize"] == 512
    with sqlite3.connect(candidate_db) as db:
        assert db.execute("SELECT value FROM probe").fetchone() == ("ok",)


def test_synthetic_page_one_is_a_valid_empty_sqlite_database(tmp_path):
    candidate = tmp_path / "synthetic.db"
    candidate.write_bytes(recover_raw_flocks_db._build_synthetic_page1(4096, 1))

    with sqlite3.connect(candidate) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT COUNT(*) FROM sqlite_schema").fetchone() == (0,)


def test_reconstruct_rejects_wal_with_different_header_page_size(tmp_path):
    source_db = tmp_path / "page-4096.db"
    with sqlite3.connect(source_db) as db:
        db.execute("PRAGMA page_size=4096")
        db.execute("VACUUM")
        db.execute("CREATE TABLE probe(value TEXT)")
        db.execute("INSERT INTO probe VALUES ('main-db')")
        db.commit()
    assert source_db.stat().st_size % 1024 == 0

    unrelated_wal = tmp_path / "unrelated.db-wal"
    unrelated_wal.write_bytes(_build_wal([(1, 1, ord("W"))], page_size=1024))

    with pytest.raises(ValueError, match="does not match"):
        recover_raw_flocks_db.reconstruct_sqlite_candidate(
            source_db,
            unrelated_wal,
            tmp_path / "candidate.db",
        )


def test_successful_cli_path_preserves_sidecars_and_compatibility_output(
    tmp_path,
    monkeypatch,
    capsys,
):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    wal_path = data_dir / "flocks.db-wal"
    shm_path = data_dir / "flocks.db-shm"
    wal_path.write_bytes(b"live-wal")
    shm_path.write_bytes(b"live-shm")
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    artifacts_dir = tmp_path / "artifacts"
    output_db = tmp_path / "recovered.db"
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    def fake_recovery(raw_path, wal_path, recovery_dir, *, prefix):
        assert raw_path == damaged_db
        assert wal_path is None
        recovery_dir.mkdir(parents=True)
        artifact_paths = recover_raw_flocks_db._recovery_artifact_paths(recovery_dir, prefix)
        candidate_db, recover_sql, extracted_db, recovered_db, summary_path = artifact_paths
        recovered_db.write_bytes(b"recovered-db")
        summary_path.write_text("recovery=ok\n", encoding="utf-8")
        return recover_raw_flocks_db.RecoveryArtifacts(
            recovery_dir=recovery_dir,
            candidate_db=candidate_db,
            recover_sql=recover_sql,
            extracted_db=extracted_db,
            recovered_db=recovered_db,
            summary_path=summary_path,
            lost_and_found_table="lost_and_found",
            pagesize=4096,
            wal_frames=0,
            wal_final_db_pages=0,
            copied_rows={},
        )

    monkeypatch.setattr(recover_raw_flocks_db, "recover_raw_storage_db", fake_recovery)

    assert (
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--output",
                str(output_db),
                "--artifacts-dir",
                str(artifacts_dir),
                "--prefix",
                "safe",
            ]
        )
        == 0
    )

    assert output_db.read_bytes() == b"recovered-db"
    assert wal_path.read_bytes() == b"live-wal"
    assert shm_path.read_bytes() == b"live-shm"
    assert "removed_sidecars=none\n" in capsys.readouterr().out


def test_sqlite_recover_rejects_partial_output_on_failure(tmp_path, monkeypatch):
    candidate_db = tmp_path / "candidate.db"
    candidate_db.write_bytes(b"candidate")
    recover_sql = tmp_path / "recover.sql"

    monkeypatch.setattr(
        recover_raw_flocks_db,
        "_sqlite_recover_capability",
        lambda: (True, ""),
    )
    monkeypatch.setattr(
        recover_raw_flocks_db.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="BEGIN;\n",
            stderr="sql error: no such table: sqlite_dbpage",
        ),
    )

    with pytest.raises(RuntimeError, match="no such table: sqlite_dbpage"):
        recover_raw_flocks_db._run_sqlite_recover(
            candidate_db,
            recover_sql,
            lost_and_found_table="lost_and_found",
        )

    assert recover_sql.exists() is False


def test_real_recovery_preserves_auth_and_custom_tables(tmp_path, monkeypatch):
    _require_sqlite_recover()

    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    source_db = tmp_path / "source.db"
    with sqlite3.connect(source_db) as db:
        db.executescript(
            """
            CREATE TABLE storage (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO storage VALUES ('probe', '{}', 'json', 'now', 'now');
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                status TEXT NOT NULL DEFAULT 'active',
                must_reset_password INTEGER NOT NULL DEFAULT 0,
                tenant_ids TEXT NOT NULL DEFAULT '[]',
                asset_groups TEXT NOT NULL DEFAULT '[]',
                temp_password_expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            );
            INSERT INTO users VALUES (
                'u1', 'admin', 'hash', 'admin', 'active', 0, '[]', '[]', NULL, 'now', 'now', NULL
            );
            CREATE TABLE user_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO user_sessions VALUES ('session-1', 'u1', 'later', 'now', 'now');
            CREATE TABLE custom_records (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE INDEX idx_custom_records_value ON custom_records(value);
            CREATE VIEW custom_record_values AS SELECT value FROM custom_records;
            CREATE TABLE custom_audit (record_id INTEGER NOT NULL, value TEXT NOT NULL);
            CREATE TRIGGER custom_records_audit
            AFTER INSERT ON custom_records
            BEGIN
                INSERT INTO custom_audit VALUES (NEW.id, NEW.value);
            END;
            INSERT INTO custom_records(value) VALUES ('preserved');
            CREATE TABLE lost_and_found (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO lost_and_found(value) VALUES ('business-table');
            PRAGMA user_version=7;
            PRAGMA application_id=4242;
            """
        )
        db.commit()

    artifacts_dir = tmp_path / "artifacts"
    output_db = tmp_path / "recovered.db"
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    assert (
        recover_raw_flocks_db.main(
            [
                str(source_db),
                "--output",
                str(output_db),
                "--artifacts-dir",
                str(artifacts_dir),
                "--prefix",
                "real",
            ]
        )
        == 0
    )

    with sqlite3.connect(output_db) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT username FROM users").fetchone() == ("admin",)
        assert db.execute("SELECT session_id FROM user_sessions").fetchone() == ("session-1",)
        assert db.execute("SELECT value FROM custom_records").fetchone() == ("preserved",)
        assert db.execute("SELECT value FROM custom_record_values").fetchone() == ("preserved",)
        assert db.execute("SELECT value FROM lost_and_found").fetchone() == ("business-table",)
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='idx_custom_records_value'"
        ).fetchone() == (1,)
        db.execute("INSERT INTO custom_records(value) VALUES ('after-recovery')")
        db.commit()
        assert db.execute(
            "SELECT value FROM custom_audit WHERE value='after-recovery'"
        ).fetchone() == ("after-recovery",)
        assert db.execute("PRAGMA user_version").fetchone() == (7,)
        assert db.execute("PRAGMA application_id").fetchone() == (4242,)


def test_real_wal_recovery_preserves_committed_row(tmp_path, monkeypatch):
    _require_sqlite_recover()

    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    live_db = tmp_path / "snapshot-source.db"
    source = sqlite3.connect(live_db)
    try:
        assert source.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.executescript(
            """
            CREATE TABLE storage (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE wal_business_records (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        source.commit()
        assert source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        source.execute("INSERT INTO storage VALUES ('wal-row', '{}', 'json', 'now', 'now')")
        source.execute("INSERT INTO wal_business_records(value) VALUES ('wal-only')")
        source.commit()

        raw_copy = tmp_path / "offline-copy.db"
        wal_copy = tmp_path / "offline-copy.db-wal"
        shutil.copy2(live_db, raw_copy)
        shutil.copy2(live_db.with_name(f"{live_db.name}-wal"), wal_copy)
        main_only_copy = tmp_path / "main-only.db"
        shutil.copy2(live_db, main_only_copy)
        with sqlite3.connect(main_only_copy) as main_only:
            assert main_only.execute("SELECT COUNT(*) FROM wal_business_records").fetchone() == (
                0,
            )

        output_db = tmp_path / "wal-recovered.db"
        monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
        assert (
            recover_raw_flocks_db.main(
                [
                    str(raw_copy),
                    "--wal",
                    str(wal_copy),
                    "--output",
                    str(output_db),
                    "--artifacts-dir",
                    str(tmp_path / "wal-artifacts"),
                    "--prefix",
                    "wal",
                ]
            )
            == 0
        )
    finally:
        source.close()

    with sqlite3.connect(output_db) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT key FROM storage WHERE key='wal-row'").fetchone() == (
            "wal-row",
        )
        assert db.execute("SELECT value FROM wal_business_records").fetchone() == ("wal-only",)


def test_real_recovery_preserves_fts_virtual_table(tmp_path, monkeypatch):
    _require_sqlite_recover()

    source_db = tmp_path / "fts-source.db"
    with sqlite3.connect(source_db) as db:
        try:
            db.execute("CREATE VIRTUAL TABLE memory_search USING fts5(content)")
        except sqlite3.OperationalError:
            pytest.skip("SQLite was built without FTS5")
        db.execute("INSERT INTO memory_search(content) VALUES ('recoverable memory')")
        db.commit()

    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    output_db = tmp_path / "fts-recovered.db"
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    assert (
        recover_raw_flocks_db.main(
            [
                str(source_db),
                "--output",
                str(output_db),
                "--artifacts-dir",
                str(tmp_path / "fts-artifacts"),
                "--prefix",
                "fts",
            ]
        )
        == 0
    )

    with sqlite3.connect(output_db) as db:
        assert db.execute(
            "SELECT content FROM memory_search WHERE memory_search MATCH 'recoverable'"
        ).fetchone() == ("recoverable memory",)


def test_failed_normalized_build_does_not_publish_output(tmp_path, monkeypatch):
    extracted_db = tmp_path / "extracted.db"
    with sqlite3.connect(extracted_db) as db:
        db.executescript(recover_raw_flocks_db.STORAGE_DDL)
        db.commit()

    def fail_validation(*_args, **_kwargs):
        raise RuntimeError("injected normalized build failure")

    monkeypatch.setattr(
        recover_raw_flocks_db,
        "_validate_recovered_db",
        fail_validation,
    )
    output_db = tmp_path / "recovered.db"

    with pytest.raises(RuntimeError, match="injected normalized build failure"):
        recover_raw_flocks_db.build_normalized_recovery_db(extracted_db, output_db)

    assert output_db.exists() is False


def test_unattributed_lost_rows_are_retained_without_guessing_destination(tmp_path):
    extracted_db = tmp_path / "extracted.db"
    lost_table = "flocks_recovery_lost_test"
    with sqlite3.connect(extracted_db) as db:
        db.executescript(recover_raw_flocks_db.STORAGE_DDL)
        db.execute(
            "INSERT INTO storage VALUES (?, ?, ?, ?, ?)",
            ("session:existing", '"direct"', "json", "now", "now"),
        )
        db.executescript(
            """
            CREATE TABLE storage_audit(key TEXT NOT NULL);
            CREATE TRIGGER storage_insert_audit
            AFTER INSERT ON storage
            BEGIN
                INSERT INTO storage_audit VALUES (NEW.key);
            END;
            """
        )
        lost_columns = ", ".join(f"c{index} TEXT" for index in range(24))
        db.execute(f"CREATE TABLE {lost_table} (nfield INTEGER, {lost_columns})")
        placeholders = ", ".join("?" for _ in range(25))
        db.executemany(
            f"INSERT INTO {lost_table} VALUES ({placeholders})",
            [
                (6, "ignored", "x", "x", "x", "x", *(None for _ in range(19))),
                (
                    5,
                    "session:existing",
                    '"lost"',
                    "json",
                    "old",
                    "old",
                    *(None for _ in range(19)),
                ),
                (
                    5,
                    "session:new",
                    '"recovered"',
                    "json",
                    "old",
                    "old",
                    *(None for _ in range(19)),
                ),
            ],
        )
        db.commit()

    output_db = tmp_path / "recovered.db"
    counts = recover_raw_flocks_db.build_normalized_recovery_db(
        extracted_db,
        output_db,
        lost_and_found_table=lost_table,
    )

    assert counts["storage"] == 1
    with sqlite3.connect(output_db) as db:
        assert db.execute(
            "SELECT value FROM storage WHERE key='session:existing'"
        ).fetchone() == ('"direct"',)
        assert db.execute("SELECT value FROM storage WHERE key='session:new'").fetchone() is None
        assert db.execute(f"SELECT COUNT(*) FROM {lost_table}").fetchone() == (3,)
        assert db.execute("SELECT COUNT(*) FROM storage_audit").fetchone() == (0,)
        db.execute(
            "INSERT INTO storage VALUES (?, ?, ?, ?, ?)",
            ("session:after", '"normal"', "json", "now", "now"),
        )
        db.commit()
        assert db.execute("SELECT key FROM storage_audit").fetchone() == ("session:after",)


def test_exact_backup_accepts_legacy_schema_and_narrow_lost_table(tmp_path):
    """Current schema assumptions must not block an otherwise valid recovered DB."""
    extracted_db = tmp_path / "legacy-extracted.db"
    lost_table = "flocks_recovery_lost_narrow"
    orphan_table = "flocks_recovery_lost_orphan"
    with sqlite3.connect(extracted_db) as db:
        db.execute(
            "CREATE TABLE usage_records(id TEXT PRIMARY KEY, legacy_value TEXT NOT NULL)"
        )
        db.execute("INSERT INTO usage_records VALUES ('legacy-1', 'preserve')")
        db.execute(
            f"CREATE TABLE {lost_table} "
            "(rootpgno INTEGER, pgno INTEGER, nfield INTEGER, c0 TEXT, c1 TEXT, c2 TEXT, c3 TEXT, c4 TEXT)"
        )
        db.execute(
            f"INSERT INTO {lost_table} VALUES (1, 2, 5, ?, ?, ?, ?, ?)",
            ("https://example.test/path", "v", "t", "created", "updated"),
        )
        orphan_columns = ", ".join(f"c{index} TEXT" for index in range(24))
        db.execute(
            f"CREATE TABLE {orphan_table} "
            f"(rootpgno INTEGER, pgno INTEGER, nfield INTEGER, {orphan_columns})"
        )
        orphan_values = (
            5,
            6,
            24,
            "txe_orphan",
            "missing_scheduler",
            *(None for _ in range(22)),
        )
        db.execute(
            f"INSERT INTO {orphan_table} VALUES ({', '.join('?' for _ in orphan_values)})",
            orphan_values,
        )
        db.commit()

    output_db = tmp_path / "legacy-recovered.db"
    counts = recover_raw_flocks_db.build_normalized_recovery_db(
        extracted_db,
        output_db,
        lost_and_found_table=lost_table,
    )

    assert counts["usage_records"] == 1
    assert counts["storage"] == 0
    with sqlite3.connect(output_db) as db:
        assert db.execute("PRAGMA table_info(usage_records)").fetchall() == [
            (0, "id", "TEXT", 0, None, 1),
            (1, "legacy_value", "TEXT", 1, None, 0),
        ]
        assert db.execute("SELECT * FROM usage_records").fetchone() == (
            "legacy-1",
            "preserve",
        )
        assert db.execute(f"SELECT COUNT(*) FROM {lost_table}").fetchone() == (1,)
        assert db.execute(f"SELECT COUNT(*) FROM {orphan_table}").fetchone() == (1,)
        assert not db.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='task_executions'"
        ).fetchone()
        assert not db.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='idx_usage_provider'"
        ).fetchone()


@pytest.mark.parametrize(
    "mutation",
    ["delete_row", "update_row", "rowid_change", "drop_view"],
)
def test_recovered_db_validator_fails_on_unknown_data_or_schema_loss(tmp_path, mutation):
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"
    with sqlite3.connect(source_db) as source:
        source.executescript(recover_raw_flocks_db.STORAGE_DDL)
        source.executescript(
            """
                CREATE TABLE plugin_records(id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO plugin_records(value) VALUES ('preserve-me');
                CREATE TABLE plugin_heap(value TEXT NOT NULL);
                INSERT INTO plugin_heap(value) VALUES ('same-visible-value');
                CREATE VIEW plugin_record_values AS SELECT value FROM plugin_records;
            """
        )
        source.commit()
        with sqlite3.connect(target_db) as target:
            source.backup(target)

    with sqlite3.connect(target_db) as target:
        if mutation == "delete_row":
            target.execute("DELETE FROM plugin_records")
        elif mutation == "update_row":
            target.execute("UPDATE plugin_records SET value='changed'")
        elif mutation == "rowid_change":
            target.execute("DELETE FROM plugin_heap")
            target.execute(
                "INSERT INTO plugin_heap(rowid, value) VALUES (99, 'same-visible-value')"
            )
        else:
            target.execute("DROP VIEW plugin_record_values")
        target.commit()

    with sqlite3.connect(source_db) as source, sqlite3.connect(target_db) as target:
        with pytest.raises(sqlite3.DatabaseError):
            recover_raw_flocks_db._validate_recovered_db(source, target)


def test_validator_skips_unavailable_virtual_table_module(tmp_path):
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"
    with sqlite3.connect(source_db) as source:
        source.execute("CREATE TABLE plugin_records(id INTEGER PRIMARY KEY, value TEXT)")
        source.execute("INSERT INTO plugin_records(value) VALUES ('preserved')")
        source.execute("PRAGMA writable_schema=ON")
        source.execute(
            "INSERT INTO sqlite_schema(type, name, tbl_name, rootpage, sql) "
            "VALUES ('table', 'plugin_vtab', 'plugin_vtab', 0, "
            "'CREATE VIRTUAL TABLE plugin_vtab USING unavailable_module(value)')"
        )
        source.execute("PRAGMA writable_schema=OFF")
        source.commit()
        with sqlite3.connect(target_db) as target:
            source.backup(target)

    with sqlite3.connect(source_db) as source, sqlite3.connect(target_db) as target:
        recover_raw_flocks_db._validate_recovered_db(source, target)


def test_failed_final_copy_does_not_publish_output(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    source.write_bytes(b"recovered-content")
    output = tmp_path / "output.db"

    def fail_copy(_source, destination):
        destination.write(b"part")
        raise OSError("injected copy failure")

    monkeypatch.setattr(recover_raw_flocks_db.shutil, "copyfileobj", fail_copy)

    with pytest.raises(OSError, match="injected copy failure"):
        recover_raw_flocks_db._copy_file_exclusive(source, output)

    assert output.exists() is False


def test_output_created_after_validation_is_not_overwritten(tmp_path, monkeypatch):
    data_dir = tmp_path / "live-data"
    data_dir.mkdir()
    damaged_db = tmp_path / "damaged.db"
    damaged_db.write_bytes(b"damaged-db")
    artifacts_dir = tmp_path / "artifacts"
    output_db = tmp_path / "recovered.db"
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    def fake_recovery(raw_path, wal_path, recovery_dir, *, prefix):
        assert raw_path == damaged_db
        assert wal_path is None
        recovery_dir.mkdir(parents=True)
        artifact_paths = recover_raw_flocks_db._recovery_artifact_paths(recovery_dir, prefix)
        candidate_db, recover_sql, extracted_db, recovered_db, summary_path = artifact_paths
        recovered_db.write_bytes(b"recovered-db")
        summary_path.write_text("recovery=ok\n", encoding="utf-8")
        output_db.write_bytes(b"late-output")
        return recover_raw_flocks_db.RecoveryArtifacts(
            recovery_dir=recovery_dir,
            candidate_db=candidate_db,
            recover_sql=recover_sql,
            extracted_db=extracted_db,
            recovered_db=recovered_db,
            summary_path=summary_path,
            lost_and_found_table="lost_and_found",
            pagesize=4096,
            wal_frames=0,
            wal_final_db_pages=0,
            copied_rows={},
        )

    monkeypatch.setattr(recover_raw_flocks_db, "recover_raw_storage_db", fake_recovery)

    with pytest.raises(FileExistsError):
        recover_raw_flocks_db.main(
            [
                str(damaged_db),
                "--output",
                str(output_db),
                "--artifacts-dir",
                str(artifacts_dir),
                "--prefix",
                "safe",
            ]
        )

    assert output_db.read_bytes() == b"late-output"
