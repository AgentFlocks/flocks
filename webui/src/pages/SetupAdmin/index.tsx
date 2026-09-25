import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/contexts/AuthContext';
import PasswordInput from '@/components/common/PasswordInput';
import AuthLayout from '@/components/layout/AuthLayout';

export default function SetupAdminPage() {
  const { t } = useTranslation('auth');
  const { bootstrapAdmin } = useAuth();
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

  return (
    <AuthLayout>
      <form
        onSubmit={onSubmit}
        className="w-full max-w-lg bg-surface border border-line rounded-card p-6 shadow-sm space-y-4"
      >
        <div>
          <h1 className="text-title-page font-bold text-fg">{t('setup.title')}</h1>
          <p className="text-sm text-fg-muted mt-1">{t('setup.description')}</p>
        </div>
        <div>
          <label className="text-sm text-fg-secondary block mb-1">{t('setup.adminUsername')}</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="ui-input w-full border border-line-control rounded-control px-3  focus:border-action"
            required
          />
        </div>
        <div>
          <label className="text-sm text-fg-secondary block mb-1">{t('setup.adminPassword')}</label>
          <PasswordInput
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
          />
        </div>
        <div>
          <label className="text-sm text-fg-secondary block mb-1">{t('fields.confirmPassword')}</label>
          <PasswordInput
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            minLength={8}
          />
        </div>
        {error && (
          <div className="text-sm text-danger bg-danger-soft border border-danger rounded-control px-3 py-2">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="ui-btn ui-btn-primary ui-btn-lg w-full"
        >
          {submitting ? t('setup.submitting') : t('setup.submitButton')}
        </button>
      </form>
    </AuthLayout>
  );
}
