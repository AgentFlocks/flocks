"""
Native Engine Metadata

The native engine executes inline via SessionLoop._run_loop() — no wrapper class.
This module only exposes static metadata so GET /api/engine/list can include it
as the always-present first entry.
"""

NATIVE_ENGINE_META: dict = {
    "id": "native",
    "name": "Flocks Native",
    "description": "Flocks 原生异步循环，多会话并发优先",
}
