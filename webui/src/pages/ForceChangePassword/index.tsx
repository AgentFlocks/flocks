import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/contexts/AuthContext';
import { authApi } from '@/api/auth';
import PasswordInput from '@/components/common/PasswordInput';
import AuthLayout from '@/components/layout/AuthLayout';

// Keep the success notice on screen briefly before the auth refresh swaps this
// page out for the normal layout; otherwise the message is set on an already
// unmounted component and the user never sees it.
const SUCCESS_NOTICE_MS = 800;

export default function ForceChangePasswordPage() {
  const { t } = useTranslation('auth');
  const { user, refresh, logout } = useAuth();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      setError(t('forceChange.passwordMismatch'));
      return;
    }

    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      await authApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setSuccess(t('forceChange.success'));
      await new Promise((resolve) => setTimeout(resolve, SUCCESS_NOTICE_MS));
      await refresh();
    } catch (err: any) {
      setError(
        err?.response?.data?.message ||
          err?.response?.data?.detail ||
          err?.message ||
          t('forceChange.failed'),
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
          <h1 className="text-title-page font-bold text-fg">{t('forceChange.title')}</h1>
          <p className="text-sm text-fg-muted mt-1">
            {t('forceChange.descriptionPrefix')}
            {' '}
            <span className="font-medium text-fg-secondary">{user?.username || '-'}</span>
            {' '}
            {t('forceChange.descriptionSuffix')}
          </p>
        </div>

        <div>
          <label className="text-sm text-fg-secondary block mb-1">{t('fields.currentPassword')}</label>
          <PasswordInput
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            placeholder={t('fields.currentPasswordPlaceholder')}
            autoComplete="current-password"
            required
          />
        </div>

        <div>
          <label className="text-sm text-fg-secondary block mb-1">{t('fields.newPassword')}</label>
          <PasswordInput
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder={t('fields.newPasswordPlaceholder')}
            autoComplete="new-password"
            required
            minLength={8}
          />
        </div>

        <div>
          <label className="text-sm text-fg-secondary block mb-1">{t('fields.confirmNewPassword')}</label>
          <PasswordInput
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            placeholder={t('fields.confirmPasswordPlaceholder')}
            autoComplete="new-password"
            required
            minLength={8}
          />
        </div>

        {error && (
          <div className="text-sm text-danger bg-danger-soft border border-danger rounded-control px-3 py-2">
            {error}
          </div>
        )}
        {success && (
          <div className="text-sm text-success bg-success-soft border border-success rounded-control px-3 py-2">
            {success}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="ui-btn ui-btn-primary ui-btn-lg w-full"
        >
          {submitting ? t('forceChange.submitting') : t('forceChange.submitButton')}
        </button>

        <button
          type="button"
          onClick={() => void logout()}
          className="w-full text-sm text-fg-muted hover:text-fg-secondary"
        >
          {t('actions.logout')}
        </button>
      </form>
    </AuthLayout>
  );
}
