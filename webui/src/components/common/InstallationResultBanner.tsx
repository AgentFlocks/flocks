import { useEffect, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getInstallationResult, type InstallationResult } from '@/api/installation';
import { useAuth } from '@/contexts/AuthContext';

/** An installer can report recovery through the same neutral record as any update. */
export function InstallationResultBanner() {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const { i18n } = useTranslation();
  const [result, setResult] = useState<InstallationResult | null>(null);
  const [dismissed, setDismissed] = useState('');
  useEffect(() => {
    if (!isAdmin) { setResult(null); return; }
    let cancelled = false;
    const refresh = () => {
      void getInstallationResult(i18n.language).then((value) => {
        if (!cancelled) setResult(value);
      }).catch(() => { /* An older server has no installation-result endpoint. */ });
    };
    refresh();
    window.addEventListener('focus', refresh);
    return () => { cancelled = true; window.removeEventListener('focus', refresh); };
  }, [isAdmin, i18n.language]);
  if (!isAdmin || !result || result.id === dismissed) return null;
  const recovered = result.recovery === 'restored' || result.recovery === 'unchanged';
  const zh = i18n.language.toLowerCase().startsWith('zh');
  return (
    <aside role="alert" className={`shrink-0 border-b px-4 py-3 text-sm ${recovered
      ? 'border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100'
      : 'border-red-200 bg-red-50 text-red-950 dark:border-red-800 dark:bg-red-950 dark:text-red-100'}`}>
      <div className="flex items-start gap-3">
        <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />
        <div className="min-w-0 flex-1 break-words">
          <p className="font-semibold">{result.title}</p>
          <p className="mt-1">{result.message}</p>
          {result.retry_hint && <p className="mt-1">{result.retry_hint}</p>}
          {result.detail && <details className="mt-2">
            <summary className="cursor-pointer underline underline-offset-2">{zh ? '查看失败原因' : 'View failure details'}</summary>
            <p className="mt-1 whitespace-pre-wrap break-all">{result.detail}</p>
          </details>}
        </div>
        <button type="button" onClick={() => setDismissed(result.id)} className="-mr-2 -mt-1 flex h-11 w-11 shrink-0 items-center justify-center rounded hover:bg-black/5 dark:hover:bg-white/10"
          aria-label={zh ? '关闭安装提示' : 'Dismiss installation notice'}>
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>
    </aside>
  );
}
