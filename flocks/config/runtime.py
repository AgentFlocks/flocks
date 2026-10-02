"""Context-local configuration replacement, without writing configuration files."""
from contextvars import ContextVar
from typing import Any

runtime_config: ContextVar[Any] = ContextVar("runtime_config", default=None)
