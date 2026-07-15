const CHUNK_RELOAD_STORAGE_KEY = 'flocks:chunk-load-reload';

type ReloadPage = () => void;

const CHUNK_LOAD_ERROR_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /loading (?:css )?chunk .+ failed/i,
  /unable to preload css/i,
];

export function isChunkLoadError(error: unknown): boolean {
  const candidate = error && typeof error === 'object'
    ? error as { name?: unknown; message?: unknown }
    : null;
  const name = candidate && typeof candidate.name === 'string' ? candidate.name : '';
  const message = candidate && typeof candidate.message === 'string'
    ? candidate.message
    : typeof error === 'string' ? error : '';
  return name === 'ChunkLoadError' || CHUNK_LOAD_ERROR_PATTERNS.some((pattern) => pattern.test(message));
}

function errorFingerprint(error: unknown): string {
  if (error instanceof Error) {
    return `${error.name}:${error.message}`.slice(0, 1000);
  }
  if (typeof error === 'string') {
    return error.slice(0, 1000);
  }
  return 'unknown-chunk-load-error';
}

export function reloadOnceForChunkLoadError(
  error: unknown,
  reload: ReloadPage = () => window.location.reload(),
): boolean {
  const fingerprint = errorFingerprint(error);
  try {
    if (window.sessionStorage.getItem(CHUNK_RELOAD_STORAGE_KEY) === fingerprint) {
      return false;
    }
    window.sessionStorage.setItem(CHUNK_RELOAD_STORAGE_KEY, fingerprint);
  } catch {
    // Without session storage we cannot safely prevent a reload loop.
    return false;
  }

  reload();
  return true;
}

export function recoverLazyLoad<T>(promise: Promise<T>, reload?: ReloadPage): Promise<T> {
  return promise.catch((error: unknown) => {
    if (isChunkLoadError(error)) {
      reloadOnceForChunkLoadError(error, reload);
    }
    throw error;
  });
}

export function retryChunkLoad(): void {
  window.location.reload();
}

let preloadRecoveryInstalled = false;

export function installVitePreloadErrorRecovery(): void {
  if (preloadRecoveryInstalled || typeof window === 'undefined') return;
  preloadRecoveryInstalled = true;

  window.addEventListener('vite:preloadError', (event) => {
    const preloadEvent = event as Event & { payload?: unknown };
    if (reloadOnceForChunkLoadError(preloadEvent.payload)) {
      event.preventDefault();
    }
  });
}
