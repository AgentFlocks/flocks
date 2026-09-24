/**
 * Extract a human-readable error message from an axios error or any error object.
 *
 * The backend uses a custom exception handler that returns:
 *   { "error": "HTTPException", "message": "..." }
 *
 * Standard FastAPI/Starlette errors use:
 *   { "detail": "..." }
 *
 * Pydantic validation errors (HTTP 422) use a list shape:
 *   { "detail": [{ "loc": [...], "msg": "...", "type": "..." }, ...] }
 *
 * This helper checks all known fields before falling back to err.message.
 */
export function extractErrorMessage(err: unknown, fallback = '操作失败'): string {
  if (!err) return fallback;
  const e = err as any;
  const asMessage = (value: unknown): string | undefined => (
    typeof value === 'string' && value.trim() ? value : undefined
  );
  const detail = e?.response?.data?.detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map((d: any) => {
      const message = asMessage(d) || asMessage(d?.msg);
      if (message) return message;
      try {
        return d == null ? undefined : JSON.stringify(d);
      } catch {
        return undefined;
      }
    }).filter(Boolean);
    if (msgs.length > 0) return msgs.join('; ');
  }
  return asMessage(detail)
    || asMessage(e?.response?.data?.message)
    || asMessage(e?.message)
    || asMessage(err)
    || fallback;
}
