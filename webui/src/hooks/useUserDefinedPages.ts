import { useCallback, useEffect, useRef, useState } from 'react';
import i18n from '@/i18n';
import { userDefinedPagesAPI, type UserDefinedPageListItem } from '@/api/userDefinedPages';

export function useUserDefinedPages() {
  const [pages, setPages] = useState<UserDefinedPageListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadingRef = useRef(false);
  const lastRefreshRef = useRef(0);

  const fetchPages = useCallback(async (silent = false) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    if (!silent) setLoading(true);
    setError(null);
    try {
      const response = await userDefinedPagesAPI.list(true);
      setPages(Array.isArray(response.data) ? response.data : []);
    } catch (err: unknown) {
      setPages([]);
      setError(err instanceof Error ? err.message : i18n.t('nav.fetchFailed', { ns: 'userDefinedPage' }));
    } finally {
      loadingRef.current = false;
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchPages();
  }, [fetchPages]);

  const refreshOnResume = useCallback((force = false) => {
    const now = Date.now();
    if (!force && now - lastRefreshRef.current < 1000) return;
    lastRefreshRef.current = now;
    void fetchPages(true);
  }, [fetchPages]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        refreshOnResume(false);
      }
    };
    const onFocus = () => {
      refreshOnResume(false);
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onFocus);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onFocus);
    };
  }, [refreshOnResume]);

  return {
    pages,
    loading,
    error,
    refetch: () => fetchPages(),
  };
}
