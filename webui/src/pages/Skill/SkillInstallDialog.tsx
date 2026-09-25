import { pageActionLabel } from '../pageActionLabel';
import PageDialog from '../PageDialog';
import { useState } from 'react';
import { IconCloudDownload, IconX, IconInfoCircle } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { extractErrorMessage } from '@/utils/error';
import { skillAPI, Skill } from '@/api/skill';
import { useToast } from '@/components/common/Toast';

interface SkillInstallDialogProps {
  onClose: () => void;
  onInstalled: (skill: Skill) => void;
}

const SOURCE_EXAMPLES = [
  { label: 'clawhub', value: 'clawhub:github' },
  { label: 'GitHub', value: 'github:owner/repo' },
  {
    label: 'SafeSkill',
    value: 'safeskill://tbx/6ef3925b1f6245bcbd7da39f23c28652/onesig-use@1.0.0',
  },
  { label: 'URL', value: 'https://raw.githubusercontent.com/...' },
];

export default function SkillInstallDialog({ onClose, onInstalled }: SkillInstallDialogProps) {
  const { t } = useTranslation('skill');
  const toast = useToast();

  const [source, setSource] = useState('');
  const [installing, setInstalling] = useState(false);

  const handleInstall = async () => {
    const trimmed = source.trim();
    if (!trimmed) return;

    setInstalling(true);
    try {
      const res = await skillAPI.install({ source: trimmed });
      if (!res.data.success) {
        toast.error(t('installFailed'), res.data.error || '');
        return;
      }

      const skillName = res.data.skill_name;

      // Warn if there are missing deps
      if (skillName) {
        try {
          const detailRes = await skillAPI.get(skillName);
          const installedSkill = detailRes.data;
          if (installedSkill.eligible === false && installedSkill.missing?.length) {
            toast.warning(
              t('installDialog.missingDepsWarning', {
                name: skillName,
                missing: installedSkill.missing.join(', '),
              }),
              t('installDialog.runInstallDeps'),
            );
          } else {
            toast.success(t('installDialog.success', { name: skillName }));
          }
          onInstalled(installedSkill);
        } catch {
          toast.success(t('installDialog.success', { name: skillName }));
          onInstalled({ name: skillName, description: '', location: res.data.location || '' });
        }
      }

      onClose();
    } catch (err: unknown) {
      // 422/409 carry the installer's reason in the response body; err.message is only axios' status line
      toast.error(
        t('installFailed'),
        extractErrorMessage(err, err instanceof Error ? err.message : String(err)),
      );
    } finally {
      setInstalling(false);
    }
  };

  return (
    <PageDialog onClose={installing ? undefined : onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-overlay-backdrop backdrop-blur-sm"


    >
      <div className="bg-surface rounded-card shadow-overlay w-full max-w-modal mx-4 overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-line">
          <div className="w-8 h-8 rounded-control bg-danger-soft flex items-center justify-center">
            <IconCloudDownload aria-hidden="true" className="w-4 h-4 text-danger" />
          </div>
          <h2 className="text-title-section font-semibold text-fg flex-1">
            {t('installDialog.title')}
          </h2>
          <button aria-label={pageActionLabel('close')}
            onClick={onClose}
            className="ui-btn-icon inline-flex items-center justify-center p-1.5 hover:bg-hover rounded-control transition-colors"
          >
            <IconX aria-hidden="true" className="w-4 h-4 text-fg-muted" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          {/* Source input */}
          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1.5">
              {t('installDialog.sourceLabel')}
            </label>
            <textarea
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder={t('installDialog.sourcePlaceholder')}
              rows={3}
              className="ui-textarea w-full px-3  border border-line-control rounded-control text-sm font-mono
 resize-none"
              autoFocus
            />
            {/* Quick fill examples */}
            <div className="flex flex-wrap gap-1.5 mt-2">
              {SOURCE_EXAMPLES.map((ex) => (
                <button
                  key={ex.label}
                  onClick={() => setSource(ex.value)}
                  className="text-xs px-2 py-0.5 rounded-pill border border-line
 text-fg-muted hover:text-danger hover:border-danger
 transition-colors font-mono"
                >
                  {ex.label}
                </button>
              ))}
            </div>
          </div>

          {/* Hint */}
          <div className="flex gap-2 p-3 bg-danger-soft rounded-control">
            <IconInfoCircle aria-hidden="true" className="w-4 h-4 text-danger shrink-0 mt-0.5" />
            <p className="text-xs text-danger ">
              {t('installDialog.sourceHint')}
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex gap-2 justify-end px-6 py-4 border-t border-line bg-subtle">
          <button
            onClick={onClose}
            disabled={installing}
            className="px-4 py-2 text-sm text-fg-secondary hover:text-fg
 border border-line-control rounded-control hover:bg-hover transition-colors"
          >
            {t('installDialog.cancel')}
          </button>
          <button
            onClick={handleInstall}
            disabled={!source.trim() || installing}
            className="flex items-center gap-2 px-4 py-2 text-sm text-action-fg
 bg-action hover:bg-action-hover rounded-control transition-colors
 disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed"
          >
            <IconCloudDownload aria-hidden="true" className="w-4 h-4" />
            {installing ? t('installDialog.installing') : t('installDialog.install')}
          </button>
        </div>
      </div>
    </PageDialog>
  );
}
