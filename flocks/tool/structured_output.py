"""Private, bounded data channel for native consumers of Tool.execute.

Display output still follows normal truncation and audit/hooks. This channel
never reads output_path, goes into ToolResult serialization, or bypasses gates.
"""
import json
import math

MAX_BYTES = 8 * 1024 * 1024
MAX_NODES = 100_000
MAX_DEPTH = 64


class StructuredOutputError(ValueError):
    pass


def bounded_copy(value):
    remaining, nodes, active = MAX_BYTES, MAX_NODES, set()

    def copy(item, depth=0):
        nonlocal remaining, nodes
        nodes -= 1
        if nodes < 0 or depth > MAX_DEPTH:
            raise StructuredOutputError('limit')
        kind = type(item)
        if kind in (dict, list):
            if id(item) in active or len(item) > nodes:
                raise StructuredOutputError('limit')
            active.add(id(item))
            remaining -= 2 + len(item) * (2 if kind is dict else 1)
            if remaining < 0:
                raise StructuredOutputError('limit')
            try:
                if kind is list:
                    return [copy(child, depth + 1) for child in item]
                result = {}
                for key, child in item.items():
                    if type(key) is not str:
                        raise StructuredOutputError('unsupported')
                    result[copy(key, depth + 1)] = copy(child, depth + 1)
                return result
            finally:
                active.remove(id(item))
        if kind not in (str, int, float, bool, type(None)):
            raise StructuredOutputError('unsupported')
        if (kind is str and len(item) > remaining) or (kind is int and item.bit_length() > 16384):
            raise StructuredOutputError('limit')
        if kind is float and not math.isfinite(item):
            raise StructuredOutputError('unsupported')
        try:
            remaining -= len(json.dumps(item, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        except (ValueError, UnicodeError):
            raise StructuredOutputError('unsupported') from None
        if remaining < 0:
            raise StructuredOutputError('limit')
        return item

    return copy(value)


class OutputCapture:
    def __init__(self, tool_name):
        self.tool_name = tool_name
        self.result = self.value = None
        self.error = None

    def accept(self, tool_name, result):
        if tool_name != self.tool_name:
            return
        self.result, self.value, self.error = result, None, None
        if not result.success:
            return
        if result.truncated:
            self.error = 'already_truncated'
            return
        try:
            self.value = bounded_copy(result.output)
        except StructuredOutputError as exc:
            self.error = str(exc)  # fixed codes, never body or external errors

    def take(self, result):
        try:
            if self.result is not result:
                raise StructuredOutputError('unavailable')
            if self.error:
                raise StructuredOutputError(self.error)
            if not result.success:
                raise StructuredOutputError('tool_failed')
            return self.value
        finally:
            self.result = self.value = self.error = None


def capture_output(ctx, tool_name, result):
    capture = getattr(ctx, '_output_capture', None)
    if isinstance(capture, OutputCapture):
        capture.accept(tool_name, result)
