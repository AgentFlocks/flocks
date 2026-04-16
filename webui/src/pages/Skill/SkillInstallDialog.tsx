import { useState } from 'react';
import { CloudDownload, X, Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { skillAPI, Skill } from '@/api/skill';
import { useToast } from '@/components/common/Toast';

interface SkillInstallDialogProps {
  onClose: () => void;
  onInstalled: (skill: Skill) => void;
}

const SOURCE_EXAMPLES = [
  { label: 'clawhub', value: 'clawhub:github' },
  { label: 'GitHub', value: 'github:owner/repo' },
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
      toast.error(
        t('installFailed'),
        err instanceof Error ? err.message : String(err),
      );
    } finally {
      setInstalling(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}
      onKeyDown={handleKeyDown}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-100">
          <div className="w-8 h-8 rounded-lg bg-red-100 flex items-center justify-center">
            <CloudDownload className="w-4 h-4 text-red-600" />
          </div>
          <h2 className="text-lg font-semibold text-gray-900 flex-1">
            {t('installDialog.title')}
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <X className="w-4 h-4 text-gray-500" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          {/* Source input */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              {t('installDialog.sourceLabel')}
            </label>
            <textarea
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder={t('installDialog.sourcePlaceholder')}
              rows={3}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono
                         focus:outline-none focus:ring-2 focus:ring-red-500 resize-none"
              autoFocus
            />
            {/* Quick fill examples */}
            <div className="flex flex-wrap gap-1.5 mt-2">
              {SOURCE_EXAMPLES.map((ex) => (
                <button
                  key={ex.label}
                  onClick={() => setSource(ex.value)}
                  className="text-xs px-2 py-0.5 rounded-full border border-gray-200
                             text-gray-500 hover:text-red-600 hover:border-red-300
                             transition-colors font-mono"
                >
                  {ex.label}
                </button>
              ))}
            </div>
          </div>

          {/* Hint */}
          <div className="flex gap-2 p-3 bg-red-50 rounded-lg">
            <Info className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
            <p className="text-xs text-red-700 leading-relaxed">
              {t('installDialog.sourceHint')}
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex gap-2 justify-end px-6 py-4 border-t border-gray-100 bg-gray-50">
          <button
            onClick={onClose}
            disabled={installing}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800
                       border border-gray-300 rounded-lg hover:bg-gray-100 transition-colors"
          >
            {t('installDialog.cancel')}
          </button>
          <button
            onClick={handleInstall}
            disabled={!source.trim() || installing}
            className="flex items-center gap-2 px-4 py-2 text-sm text-white
                       bg-red-600 hover:bg-red-700 rounded-lg transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <CloudDownload className="w-4 h-4" />
            {installing ? t('installDialog.installing') : t('installDialog.install')}
          </button>
        </div>
      </div>
    </div>
  );
}
