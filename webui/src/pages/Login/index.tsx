import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/contexts/AuthContext';
import PasswordInput from '@/components/common/PasswordInput';
import AuthLayout from '@/components/layout/AuthLayout';
import { useProductName } from '@/contexts/ProductNameContext';

export default function LoginPage() {
  const { t } = useTranslation('auth');
  const { login } = useAuth();
  const { productName } = useProductName();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err: any) {
      setError(
        err?.response?.data?.message ||
          err?.response?.data?.detail ||
          err?.message ||
          t('login.failed'),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthLayout>
      <form
        onSubmit={onSubmit}
        className="w-full max-w-md bg-white border border-gray-200 rounded-xl p-6 shadow-sm space-y-4 dark:border-[#4a5563] dark:bg-[#303842] dark:shadow-xl dark:shadow-black/20"
      >
        <div>
          <h1 className="text-xl font-semibold text-gray-900 dark:text-[#d7dee8]">{t('login.title', { productName })}</h1>
          <p className="text-sm text-gray-500 mt-1 dark:text-[#b8c2cc]">{t('login.description', { productName })}</p>
        </div>
        <div>
          <label className="text-sm text-gray-700 block mb-1 dark:text-[#d7dee8]">{t('fields.username')}</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-blue-500 dark:border-[#4a5563] dark:bg-[#252c35] dark:text-[#d7dee8] dark:placeholder:text-[#9aa7b4] dark:focus:border-[#539bf5]"
            placeholder={t('fields.usernamePlaceholder')}
            autoComplete="username"
            required
          />
        </div>
        <div>
          <label className="text-sm text-gray-700 block mb-1 dark:text-[#d7dee8]">{t('fields.password')}</label>
          <PasswordInput
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t('fields.passwordPlaceholder')}
            autoComplete="current-password"
            required
          />
        </div>
        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 dark:border-red-400/30 dark:bg-red-500/15 dark:text-red-200">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="w-full bg-slate-900 text-white rounded-lg py-2.5 font-medium hover:bg-slate-800 disabled:opacity-60 dark:bg-[#46515e] dark:hover:bg-[#5a6573]"
        >
          {submitting ? t('actions.loggingIn') : t('actions.login')}
        </button>
        <div className="space-y-2 text-xs text-gray-500 border-t border-gray-100 pt-3 dark:border-[#4a5563] dark:text-[#b8c2cc]">
          <div>
            {t('login.recoverUsername', { productName })}
            {' '}
            <code className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700 dark:bg-[#46515e] dark:text-[#d7dee8]">flocks admin list-users</code>
          </div>
          <div>
            {t('login.recoverPassword')}
            {' '}
            <code className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700 dark:bg-[#46515e] dark:text-[#d7dee8]">flocks admin generate-one-time-password --username admin</code>
          </div>
        </div>
      </form>
    </AuthLayout>
  );
}
