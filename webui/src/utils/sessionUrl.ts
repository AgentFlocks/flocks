/** Stable WebUI path; never use the API origin for browser navigation. */
export function sessionPath(sessionId: string): string {
  return `/sessions/${encodeURIComponent(sessionId)}`;
}

/** Validate the decoded returnTo value before feeding it to the router. */
export function safeReturnTo(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//')) return '/';
  try {
    if (/[\\\x00-\x1f]/.test(value)) return '/';
    const target = new URL(value, 'https://flocks.invalid');
    const decodedPath = decodeURIComponent(target.pathname);
    if (/[\\\x00-\x20]/.test(decodedPath) || decodedPath.startsWith('//')) return '/';
    const decodedTarget = new URL(decodedPath, 'https://flocks.invalid');
    if (target.origin !== 'https://flocks.invalid' || decodedTarget.origin !== target.origin) return '/';
    if ([target.pathname, decodedTarget.pathname].some(path => /^\/(login|setup-admin)(\/|$)/.test(path))) return '/';
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return '/';
  }
}
