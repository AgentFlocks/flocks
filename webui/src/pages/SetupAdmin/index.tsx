import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/contexts/AuthContext';
import PasswordInput from '@/components/common/PasswordInput';
import AuthLayout from '@/components/layout/AuthLayout';
import { useProductName } from '@/contexts/ProductNameContext';

const CODE_CLASS = 'rounded bg-gray-100 px-1.5 py-0.5 text-gray-700 dark:bg-[#46515e] dark:text-[#d7dee8]';

/** The first administrator is created on the server's own machine; other browsers are told how. */
function RemoteSetupNotice({ localUrl, onRefresh }: { localUrl: string | null; onRefresh: () => void }) {
  const { t } = useTranslation('auth');
  const { productName } = useProductName();
  return (
    <div className="w-full max-w-lg bg-white border border-gray-200 rounded-xl p-6 shadow-sm space-y-4 dark:border-[#4a5563] dark:bg-[#303842] dark:shadow-xl dark:shadow-black/20">
      <div>
        <h1 className="text-xl font-semibold text-gray-900 dark:text-[#d7dee8]">{t('setup.title')}</h1>
        <p className="text-sm text-gray-500 mt-1 dark:text-[#b8c2cc]">{t('setup.remoteDescription', { productName })}</p>
      </div>
      <ol className="list-decimal pl-5 space-y-3 text-sm text-gray-700 dark:text-[#d7dee8]">
        {localUrl && (
          <li>
            {t('setup.remoteLocalBrowser')}
            {' '}
            <code className={CODE_CLASS}>{localUrl}</code>
          </li>
        )}
        <li>
          {t(localUrl ? 'setup.remoteCommandOr' : 'setup.remoteCommand')}
          {' '}
          <code className={CODE_CLASS}>flocks admin generate-one-time-password</code>
          {t('setup.remoteCommandAfter')}
        </li>
      </ol>
      <button
        type="button"
        onClick={onRefresh}
        className="w-full bg-slate-900 text-white rounded-lg py-2.5 font-medium hover:bg-slate-800 dark:bg-[#46515e] dark:hover:bg-[#5a6573]"
      >
        {t('setup.remoteRefresh')}
      </button>
    </div>
  );
}

export default function SetupAdminPage() {
  const { t } = useTranslation('auth');
  const { bootstrapAdmin, setupAllowed, localSetupUrl, refresh } = useAuth();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      setError(t('setup.passwordMismatch'));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await bootstrapAdmin(username, password);
    } catch (err: any) {
      setError(
        err?.response?.data?.message ||
          err?.response?.data?.detail ||
          err?.message ||
          t('setup.failed'),
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (!setupAllowed) {
    return (
      <AuthLayout>
        <RemoteSetupNotice localUrl={localSetupUrl} onRefresh={() => void refresh()} />
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <form
        onSubmit={onSubmit}
        className="w-full max-w-lg bg-white border border-gray-200 rounded-xl p-6 shadow-sm space-y-4"
      >
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{t('setup.title')}</h1>
          <p className="text-sm text-gray-500 mt-1">{t('setup.description')}</p>
        </div>
        <div>
          <label className="text-sm text-gray-700 block mb-1">{t('setup.adminUsername')}</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
            required
          />
        </div>
        <div>
          <label className="text-sm text-gray-700 block mb-1">{t('setup.adminPassword')}</label>
          <PasswordInput
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
          />
        </div>
        <div>
          <label className="text-sm text-gray-700 block mb-1">{t('fields.confirmPassword')}</label>
          <PasswordInput
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            minLength={8}
          />
        </div>
        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="w-full bg-slate-900 text-white rounded-lg py-2.5 font-medium hover:bg-slate-800 disabled:opacity-60"
        >
          {submitting ? t('setup.submitting') : t('setup.submitButton')}
        </button>
      </form>
    </AuthLayout>
  );
}
