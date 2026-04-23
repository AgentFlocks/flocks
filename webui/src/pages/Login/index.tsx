import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/contexts/AuthContext';
import PasswordInput from '@/components/common/PasswordInput';
import AuthLayout from '@/components/layout/AuthLayout';

export default function LoginPage() {
  const { t } = useTranslation('auth');
  const { login } = useAuth();
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
        className="w-full max-w-md bg-white border border-gray-200 rounded-xl p-6 shadow-sm space-y-4"
      >
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{t('login.title')}</h1>
          <p className="text-sm text-gray-500 mt-1">{t('login.description')}</p>
        </div>
        <div>
          <label className="text-sm text-gray-700 block mb-1">{t('fields.username')}</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
            placeholder={t('fields.usernamePlaceholder')}
            autoComplete="username"
            required
          />
        </div>
        <div>
          <label className="text-sm text-gray-700 block mb-1">{t('fields.password')}</label>
          <PasswordInput
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t('fields.passwordPlaceholder')}
            autoComplete="current-password"
            required
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
          {submitting ? t('actions.loggingIn') : t('actions.login')}
        </button>
        <div className="space-y-2 text-xs text-gray-500 border-t border-gray-100 pt-3">
          <div>
            {t('login.recoverUsername')}
            {' '}
            <code className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700">flocks admin list-users</code>
          </div>
          <div>
            {t('login.recoverPassword')}
            {' '}
            <code className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700">flocks admin generate-one-time-password --username admin_user_name</code>
          </div>
        </div>
      </form>
    </AuthLayout>
  );
}
