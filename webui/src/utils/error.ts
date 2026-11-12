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
  const data = e?.response?.data;
  const detail = data?.error === 'ValidationError' && Array.isArray(data.details) ? data.details : data?.detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map((d: any) => {
      const message = asMessage(d) || asMessage(d?.msg);
      if (message) {
        const field = Array.isArray(d?.loc) ? d.loc.filter((part: unknown) => part !== 'body').join('.') : '';
        return field ? `${field}: ${message}` : message;
      }
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

/**
 * Whether a failed request was refused by this service itself, rather than answered for it by a
 * proxy in front of it.
 *
 * The service refuses in JSON: `{ error, message }` from its exception handlers, `{ detail }`
 * from the framework. A proxy standing in for a service that is down or restarting sends its own
 * page under whatever status it is set up to give: 502 or 503 on most, 404 or 403 on some. The
 * status alone does not tell the two apart, so what waits through a restart asks this before it
 * takes a 4xx for an answer that will not change.
 */
export function isServiceRefusal(err: unknown): boolean {
  const response = (err as { response?: { status?: unknown; data?: unknown } } | null | undefined)?.response;
  const status = response?.status;
  if (typeof status !== 'number' || status < 400 || status >= 500) return false;
  const data = response?.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
  const body = data as Record<string, unknown>;
  return 'detail' in body || (typeof body.error === 'string' && 'message' in body);
}
