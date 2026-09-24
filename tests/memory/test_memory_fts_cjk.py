"""memory_fts 中文检索（TOL-0471）：trigram 分词 + 短词 LIKE 兜底 + 旧分词器平滑迁移。

之前 tokenize='porter unicode61' 把 "告警处理约定" 整段当一个 token，中文关键词永远 0 结果。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flocks.storage import Storage, ensure_vector_tables, fts_search, replace_memory_file_index
from flocks.storage.vector import FTS_TOKENIZER, build_fts_query, split_fts_terms


def _file_entry(path: str) -> dict:
    return {"scope": "global", "scope_id": "", "path": path, "hash": f"hash:{path}", "mtime": 1, "size": 10}


def _chunk(path: str, text: str) -> dict:
    return {
        "id": f"chunk:{path}",
        "scope": "global",
        "scope_id": "",
        "path": path,
        "source": "memory",
        "start_line": 1,
        "end_line": 1,
        "hash": f"hash:{text}",
        "text": text,
        "embedding": None,
        "embedding_model": None,
        "embedding_dims": None,
    }


async def _seed(db_path: Path) -> None:
    await Storage.init(db_path)
    for path, text in (
        ("MEMORY.md", "今天的告警处理约定：先看 nginx 日志，再看 alerts 面板"),
        ("USER.md", "The user prefers a running alert pipeline summary"),
        ("daily/2026-08-03.md", "无关内容：周报模板"),
    ):
        await replace_memory_file_index(db_path, file_entry=_file_entry(path), chunks=[_chunk(path, text)])


def test_split_terms_and_query_shape() -> None:
    assert split_fts_terms("告警处理约定 IP alerts") == (["告警处理约定", "alerts"], ["IP"])
    assert build_fts_query("告警处理约定 alerts") == '"告警处理约定" AND "alerts"'
    assert build_fts_query("告警") is None  # 短词不进 MATCH，由 fts_search 走 LIKE


@pytest.mark.asyncio
async def test_chinese_and_english_keywords_hit(tmp_path: Path) -> None:
    db_path = tmp_path / "cjk.db"
    await _seed(db_path)

    async def paths(query: str) -> set[str]:
        return {r["path"] for r in await fts_search(db_path, "default", query)}

    assert await paths("告警处理约定") == {"MEMORY.md"}
    assert await paths("告警处理") == {"MEMORY.md"}
    assert await paths("alert") == {"MEMORY.md", "USER.md"}  # 子串匹配 alerts/alert，大小写不敏感
    assert await paths("ALERT") == {"MEMORY.md", "USER.md"}
    assert await paths("告警") == {"MEMORY.md"}  # 2 字：LIKE 兜底
    assert await paths("nginx 告警") == {"MEMORY.md"}  # MATCH + LIKE 组合
    assert await paths("不存在的词") == set()
    for r in await fts_search(db_path, "default", "告警"):
        assert r["score"] == 0.5  # 只有 LIKE 命中时的固定分


@pytest.mark.asyncio
async def test_existing_pg_memory_index_keeps_rows_and_adds_accelerators(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    async with Storage.transaction(ensure_init=False) as connection:
        await connection.executescript(
            """
            CREATE TABLE memory_fts(text TEXT NOT NULL, chunk_id TEXT PRIMARY KEY,
                path TEXT, source TEXT, scope TEXT, scope_id TEXT, start_line INTEGER, end_line INTEGER);
            INSERT INTO memory_fts VALUES ('旧库里的告警处理约定', 'c1', 'MEMORY.md', 'memory', 'global', '', 1, 1);
            """
        )

    await ensure_vector_tables(db_path)
    await ensure_vector_tables(db_path)  # 第二次是幂等的

    async with Storage.transaction(ensure_init=False) as connection:
        indexes = await connection.fetch_all("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() AND tablename = 'memory_fts'")
        assert {'memory_fts_scope', 'memory_fts_text_trgm'}.issubset({row[0] for row in indexes})
        assert await connection.fetch_one("SELECT COUNT(*) FROM memory_fts") == (1,)
    assert FTS_TOKENIZER == 'pg_trgm'

    hits = await fts_search(db_path, "default", "告警处理")
    assert [r["id"] for r in hits] == ["c1"]  # 回填的数据用新分词器能查到


@pytest.mark.asyncio
async def test_pg_search_works_when_trigram_extension_is_outside_search_path(tmp_path: Path, monkeypatch):
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    from flocks.db import registry

    # Preserve the fixture schema but hide public functions/operators. This
    # exercises the same runtime path as a role unable to install pg_trgm.
    parts = urlsplit(registry.current().dsn)
    query = dict(parse_qsl(parts.query)); query['search_path'] = query['search_path'].split(',')[0]
    monkeypatch.setattr(registry, '_settings', registry.DatabaseSettings('postgres', urlunsplit(parts._replace(query=urlencode(query)))))
    path = tmp_path / 'without-trigram'
    await _seed(path)
    async with Storage.transaction() as db:
        assert await db.fetch_one("SELECT to_regprocedure('similarity(text,text)') IS NULL") == (True,)
    hits = await fts_search(path, 'default', '告警处理')
    assert [hit['path'] for hit in hits] == ['MEMORY.md']
    assert 0.5 <= hits[0]['score'] <= 1


@pytest.mark.asyncio
async def test_keyword_rank_preserves_relevance_and_matches_long_notes(tmp_path: Path):
    path = tmp_path / 'ranked-memory'
    await Storage.init(path)
    for filename, text in (
        ('exact.md', 'durable'),
        ('long.md', 'durable ' + 'unrelated context ' * 400),
    ):
        await replace_memory_file_index(path, file_entry=_file_entry(filename), chunks=[_chunk(filename, text)])
    hits = await fts_search(path, 'default', 'durable')
    assert [hit['path'] for hit in hits] == ['exact.md', 'long.md']
    assert hits[0]['score'] == 1.0
    assert 0.5 <= hits[1]['score'] < hits[0]['score']
