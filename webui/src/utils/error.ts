const asMessage = (value: unknown): string | undefined => (
  typeof value === 'string' && value.trim() ? value : undefined
);

/** The text of a route's `detail`: the text itself, or the `message` of a structured `{ code, message, ... }`. */
const detailText = (value: unknown): string | undefined => (
  asMessage(value) ?? asMessage((value as { message?: unknown } | null | undefined)?.message)
);

/**
 * The reason the service gave in the body of a failed response, or `undefined` when it gave none.
 *
 * The backend's exception handler answers an `HTTPException` with
 *   { "error": "HTTPException", "message": <detail> }
 * so what a route passed as `detail` arrives under `message`, not under `detail`. It is text, or
 * for a structured refusal an object such as `{ "code": "...", "message": "..." }`.
 *
 * A few answers do carry `detail` (a write to a workflow the deployment manages, and anything
 * that speaks plain FastAPI/Starlette):
 *   { "detail": "..." }
 *
 * Pydantic validation errors (HTTP 422) arrive as
 *   { "error": "ValidationError", "message": "...", "details": [{ "loc": [...], "msg": "..." }, ...] }
 * or, from plain FastAPI, as the same list under `detail`.
 *
 * The catch-all answer to an unexpected fault,
 *   { "error": "InternalServerError", "message": "Internal server error" }
 * says that the request failed, not why, so it counts as no reason.
 *
 * Unlike {@link extractErrorMessage} this never falls back to the transport's own `err.message`
 * ("Request failed with status code 403", "Network Error"). Use it where the caller's fallback
 * says more than those lines do: `extractServerMessage(err) || t('saveFailed')`.
 */
export function extractServerMessage(err: unknown): string | undefined {
  const data = (err as any)?.response?.data;
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
  return detailText(detail) ?? (data?.error === 'InternalServerError' ? undefined : detailText(data?.message));
}

/**
 * Extract a human-readable error message from an axios error or any error object: the reason
 * the service gave (see {@link extractServerMessage}), else whatever line its answer carries
 * (the catch-all fault's), else the error's own message, else `fallback`.
 */
export function extractErrorMessage(err: unknown, fallback = '操作失败'): string {
  if (!err) return fallback;
  return extractServerMessage(err)
    || asMessage((err as any)?.response?.data?.message)
    || asMessage((err as any)?.message)
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
