import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/contexts/AuthContext';
import PasswordInput from '@/components/common/PasswordInput';
import AuthLayout from '@/components/layout/AuthLayout';
import { useProductName } from '@/contexts/ProductNameContext';
import { IconCircleX } from '@tabler/icons-react';

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
        className="w-full max-w-md bg-surface border border-line rounded-card p-6 shadow-sm space-y-4 "
      >
        <div>
          <h1 className="text-title-page font-bold text-fg">{t('login.title', { productName })}</h1>
          <p className="text-sm text-fg-muted mt-1 ">{t('login.description', { productName })}</p>
        </div>
        <div>
          <label htmlFor="login-username" className="ui-label">{t('fields.username')}</label>
          <input
            id="login-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="ui-input"
            placeholder={t('fields.usernamePlaceholder')}
            autoComplete="username"
            required
          />
        </div>
        <div>
          <label htmlFor="login-password" className="ui-label">{t('fields.password')}</label>
          <PasswordInput
            id="login-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t('fields.passwordPlaceholder')}
            autoComplete="current-password"
            required
          />
        </div>
        {error && (
          <div role="alert" className="flex items-start gap-2 text-sm text-danger bg-danger-soft border border-danger rounded-control px-3 py-2">
            <IconCircleX aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="ui-btn ui-btn-primary ui-btn-lg w-full"
        >
          {submitting ? t('actions.loggingIn') : t('actions.login')}
        </button>
        <div className="space-y-2 text-xs text-fg-muted border-t border-line pt-3 ">
          <div>
            {t('login.recoverUsername', { productName })}
            {' '}
            <code className="rounded-tag bg-subtle px-1.5 py-0.5 text-fg-secondary ">flocks admin list-users</code>
          </div>
          <div>
            {t('login.recoverPassword')}
            {' '}
            <code className="rounded-tag bg-subtle px-1.5 py-0.5 text-fg-secondary ">flocks admin generate-one-time-password --username admin</code>
          </div>
        </div>
      </form>
    </AuthLayout>
  );
}
