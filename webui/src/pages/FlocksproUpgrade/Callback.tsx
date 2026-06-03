import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { authApi } from '@/api/auth';
import { extractErrorMessage } from '@/utils/error';

export default function FlocksproUpgradeCallbackPage() {
  const { t } = useTranslation('flockspro');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const consoleLoginId = searchParams.get('console_login_id');
    const state = searchParams.get('state') ?? undefined;
    const passportUid = searchParams.get('passport_uid') ?? undefined;
    const loginStatus = searchParams.get('console_login_status');
    const callbackMessage = searchParams.get('message') || searchParams.get('error_code');
    if (loginStatus === 'error') {
      setError(callbackMessage || t('callback.exchangeFailed'));
      return;
    }
    if (!consoleLoginId) {
      setError(t('callback.missingConsoleLoginId'));
      return;
    }

    let cancelled = false;
    const run = async () => {
      try {
        await authApi.finishConsoleLogin(consoleLoginId, state, passportUid);
        if (!cancelled) {
          navigate('/flockspro-upgrade?login=success', { replace: true });
        }
      } catch (err) {
        if (!cancelled) {
          setError(extractErrorMessage(err, t('callback.exchangeFailed')));
        }
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [navigate, searchParams, t]);

  if (error) {
    return (
      <div className="max-w-xl mx-auto mt-20 bg-white rounded-xl border border-red-200 p-6 text-red-700">
        <h1 className="text-lg font-semibold">{t('callback.failedTitle')}</h1>
        <p className="text-sm mt-2">{error}</p>
      </div>
    );
  }

  return (
    <div className="max-w-xl mx-auto mt-20 bg-white rounded-xl border border-gray-200 p-6">
      <div className="flex items-center gap-3 text-gray-700">
        <Loader2 className="w-5 h-5 animate-spin" />
        <span>{t('callback.processing')}</span>
      </div>
    </div>
  );
}

