from __future__ import annotations

import asyncio

import pytest

from flocks.server.config_mutation import serialized_config_mutation


@pytest.mark.asyncio
async def test_config_mutation_lock_is_reentrant_for_nested_routes():
    calls: list[str] = []

    @serialized_config_mutation
    async def inner() -> None:
        calls.append("inner")

    @serialized_config_mutation
    async def outer() -> None:
        calls.append("outer")
        await inner()

    await asyncio.wait_for(outer(), timeout=1)

    assert calls == ["outer", "inner"]


@pytest.mark.asyncio
async def test_config_mutation_lock_serializes_concurrent_tasks():
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    calls: list[str] = []

    @serialized_config_mutation
    async def mutate(name: str) -> None:
        calls.append(f"{name}:start")
        if name == "first":
            first_entered.set()
            await release_first.wait()
        calls.append(f"{name}:end")

    first = asyncio.create_task(mutate("first"))
    await first_entered.wait()
    second = asyncio.create_task(mutate("second"))
    await asyncio.sleep(0)

    assert calls == ["first:start"]
    release_first.set()
    await asyncio.gather(first, second)

    assert calls == [
        "first:start",
        "first:end",
        "second:start",
        "second:end",
    ]
