"""Private, bounded data channel for native consumers of Tool.execute.

Display output still follows normal truncation and audit/hooks. This channel
never reads output_path, goes into ToolResult serialization, or bypasses gates.
"""
import json
import math

MAX_BYTES = 8 * 1024 * 1024
MAX_NODES = 100_000
MAX_DEPTH = 64
PRIVATE_OUTPUT = '受控查询结果已通过内部数据通道传递；原始内容不在通用工具输出中展示。'
PRIVATE_ERROR = '受控查询失败；原始错误内容未公开，请查看设备状态及安全诊断记录。'


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
    def __init__(self, tool_name, *, private=False):
        self.tool_name = tool_name
        # Internal consumers sanitize their own bounded summaries. Never pass
        # their raw HTTP evidence to generic display truncation or its files.
        self.private = private
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


def is_private_capture(ctx, tool_name):
    capture = getattr(ctx, '_output_capture', None)
    return isinstance(capture, OutputCapture) and capture.tool_name == tool_name and capture.private is True


def hide_private_output(ctx, tool_name, result):
    """Mask only the display result, after its bounded in-memory capture.

    Success/truncation remain untouched: a pre-truncated or over-budget result
    still fails ``take`` rather than becoming a successful complete response.
    """
    if not is_private_capture(ctx, tool_name):
        return False
    result.output = PRIVATE_OUTPUT
    if not result.success or result.error:
        result.error = PRIVATE_ERROR
    result.metadata = {'private_output': True}
    result.title = None
    result.attachments = None
    return True
