import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { providerAPI } from '@/api/provider';
import { extractErrorMessage } from '@/utils/error';

export default function ProviderKeyCheck({
  providerId,
  credentials,
  disabled,
}: {
  providerId: string;
  credentials: { api_key?: string; base_url?: string };
  disabled?: boolean;
}) {
  const { t } = useTranslation('model');
  const [checking, setChecking] = useState(false);
  const modelListCheck = providerId === 'threatbook-cn-llm' || providerId === 'threatbook-io-llm';
  const [result, setResult] = useState<{ status: string; message: string } | null>(null);
  const version = useRef(0);
  useEffect(() => {
    version.current += 1;
    setResult(null);
    setChecking(false);
    return () => {
      version.current += 1;
    };
  }, [providerId, credentials.api_key, credentials.base_url]);
  const check = async () => {
    if (checking || disabled) return;
    const current = ++version.current;
    setChecking(true);
    setResult(null);
    try {
      const { data } = await providerAPI.checkKey(providerId, credentials);
      if (current !== version.current) return;
      setResult({
        status: data.status,
        message: t(`compact.keyCodes.${data.code}`, { defaultValue: t('compact.keyUnverified') }),
      });
    } catch (error) {
      if (current === version.current)
        setResult({ status: 'invalid', message: extractErrorMessage(error) });
    } finally {
      if (current === version.current) setChecking(false);
    }
  };
  return (
    <section aria-label={t('compact.checkKey')} className="space-y-2 pt-1">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">{t('compact.checkKey')}</h3>
          <p className="mt-1 text-xs text-gray-500">
            {t(modelListCheck ? 'compact.checkModelListHint' : 'compact.checkKeyHint')}
          </p>
        </div>
        <button
          type="button"
          onClick={check}
          disabled={checking || disabled}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-violet-600 px-3 py-1.5 text-sm text-white hover:bg-violet-700 disabled:opacity-40"
        >
          {checking && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {t(checking ? 'compact.checkingKey' : 'compact.check')}
        </button>
      </div>
      {result && (
        <p
          role={result.status === 'invalid' ? 'alert' : 'status'}
          className={`rounded-md px-3 py-2 text-xs ${result.status === 'valid' ? 'bg-green-50 text-green-700' : result.status === 'invalid' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-800'}`}
        >
          {result.message}
        </p>
      )}
    </section>
  );
}
