import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/contexts/AuthContext';
import PasswordInput from '@/components/common/PasswordInput';
import AuthLayout from '@/components/layout/AuthLayout';

export default function ForceChangePasswordPage() {
  const { t } = useTranslation('auth');
  const { user, changePassword, logout } = useAuth();
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
      await changePassword(currentPassword, newPassword);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setSuccess(t('forceChange.success'));
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
        className="w-full max-w-lg bg-white border border-gray-200 rounded-xl p-6 shadow-sm space-y-4"
      >
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{t('forceChange.title')}</h1>
          <p className="text-sm text-gray-500 mt-1">
            {t('forceChange.descriptionPrefix')}
            {' '}
            <span className="font-medium text-gray-700">{user?.username || '-'}</span>
            {' '}
            {t('forceChange.descriptionSuffix')}
          </p>
        </div>

        <div>
          <label className="text-sm text-gray-700 block mb-1">{t('fields.currentPassword')}</label>
          <PasswordInput
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            placeholder={t('fields.currentPasswordPlaceholder')}
            autoComplete="current-password"
            required
          />
        </div>

        <div>
          <label className="text-sm text-gray-700 block mb-1">{t('fields.newPassword')}</label>
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
          <label className="text-sm text-gray-700 block mb-1">{t('fields.confirmNewPassword')}</label>
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
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </div>
        )}
        {success && (
          <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
            {success}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="w-full bg-slate-900 text-white rounded-lg py-2.5 font-medium hover:bg-slate-800 disabled:opacity-60"
        >
          {submitting ? t('forceChange.submitting') : t('forceChange.submitButton')}
        </button>

        <button
          type="button"
          onClick={() => void logout()}
          className="w-full text-sm text-gray-500 hover:text-gray-700"
        >
          {t('actions.logout')}
        </button>
      </form>
    </AuthLayout>
  );
}
