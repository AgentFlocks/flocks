import { useState, useEffect, useMemo } from 'react';
import { BookOpen, Search, Plus, RefreshCw, Sparkles, FolderOpen, CloudDownload, CheckCircle, AlertTriangle, Loader2, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import { skillAPI, Skill } from '@/api/skill';
import SkillSheet from './SkillSheet';
import SkillInstallDialog from './SkillInstallDialog';
import { getLocalizedSkillDescription } from './skillDisplay';

export default function SkillPage() {
  const { t, i18n } = useTranslation('skill');
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [installingDeps, setInstallingDeps] = useState<Record<string, boolean>>({});
  const toast = useToast();

  // Sheet state
  const [sheetSkill, setSheetSkill] = useState<Skill | null>(null);
  const [showCreateSheet, setShowCreateSheet] = useState(false);
  const [showInstallDialog, setShowInstallDialog] = useState(false);

  const fetchSkills = async () => {
    try {
      setLoading(true);
      setError(null);
      // Use /status endpoint to get eligibility info
      const response = await skillAPI.status();
      setSkills(Array.isArray(response.data) ? response.data : []);
    } catch {
      // Fallback to plain list if status endpoint isn't available yet
      try {
        const fallback = await skillAPI.list();
        setSkills(Array.isArray(fallback.data) ? fallback.data : []);
      } catch (listErr: unknown) {
        setError(listErr instanceof Error ? listErr.message : t('fetchListFailed'));
        setSkills([]);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSkills();
  }, []);

  const filteredSkills = useMemo(
    () => skills.filter(skill =>
      skill.category !== 'system' && (
        skill.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (skill.description || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        (skill.description_cn || '').toLowerCase().includes(searchQuery.toLowerCase())
      )
    ),
    [skills, searchQuery],
  );

  const [refreshing, setRefreshing] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);

  const userSkills = useMemo(() => filteredSkills.filter(isUserManaged), [filteredSkills]);
  const builtinSkills = useMemo(() => filteredSkills.filter(s => !isUserManaged(s)), [filteredSkills]);

  const handleRefresh = async () => {
    if (refreshing) return;
    try {
      setRefreshing(true);
      // Ensure a minimum spin duration so the animation is clearly visible
      await Promise.all([
        skillAPI.refresh().then(() => fetchSkills()),
        new Promise((r) => setTimeout(r, 600)),
      ]);
      setRefreshDone(true);
      setTimeout(() => setRefreshDone(false), 2000);
    } catch (err: unknown) {
      toast.error(t('refreshFailed'), err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  };

  const handleSelectSkill = async (skill: Skill) => {
    try {
      const response = await skillAPI.get(skill.name);
      setSheetSkill(response.data);
    } catch (err: unknown) {
      toast.error(t('fetchDetailFailed'), err instanceof Error ? err.message : String(err));
    }
  };

  const handleInstallDeps = async (skill: Skill, e: React.MouseEvent) => {
    e.stopPropagation();
    setInstallingDeps(prev => ({ ...prev, [skill.name]: true }));
    try {
      const res = await skillAPI.installDeps(skill.name);
      const allOk = res.data.results.every(r => r.success);
      if (allOk) {
        toast.success(t('eligibility.installSuccess'));
        fetchSkills();
      } else {
        const errors = res.data.results
          .filter(r => !r.success)
          .map(r => r.error || 'unknown error')
          .join('; ');
        toast.error(t('eligibility.installFailed'), errors);
      }
    } catch (err: unknown) {
      toast.error(t('installDepsFailed'), err instanceof Error ? err.message : String(err));
    } finally {
      setInstallingDeps(prev => ({ ...prev, [skill.name]: false }));
    }
  };

  const handleInstalled = () => {
    fetchSkills();
  };

  const handleDeleteFromCard = async (skill: Skill, e: React.MouseEvent) => {
    e.stopPropagation();
    if (skill.source === 'project') return;
    if (!confirm(t('sheet.deleteConfirm', { name: skill.name }))) return;
    try {
      await skillAPI.delete(skill.name);
      fetchSkills();
    } catch (err: unknown) {
      toast.error(t('sheet.deleteFailed'), err instanceof Error ? err.message : String(err));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error}</p>
          <button onClick={fetchSkills} className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">
            {t('common:button.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<BookOpen className="w-8 h-8" />}
        action={
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder={t('searchPlaceholder')}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-48 pl-9 pr-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              />
            </div>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              title={refreshDone ? t('refreshed') : t('refresh')}
              className={`p-2 border rounded-lg transition-all ${
                refreshDone
                  ? 'border-green-300 text-green-600 bg-green-50'
                  : 'border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50'
              }`}
            >
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => setShowInstallDialog(true)}
              className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 bg-white rounded-lg hover:bg-gray-50 transition-colors"
            >
              <CloudDownload className="w-4 h-4" />
              {t('installSkill')}
            </button>
            <button
              onClick={() => setShowCreateSheet(true)}
              className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
            >
              <Plus className="w-4 h-4" />
              {t('createSkill')}
            </button>
          </div>
        }
      />

      <div className="flex-1 overflow-y-auto px-2 py-2">
        {filteredSkills.length === 0 ? (
          <EmptyState
            icon={<BookOpen className="w-16 h-16" />}
            title={searchQuery ? t('emptyState.noResults') : t('emptyState.noSkills')}
            description={searchQuery ? t('emptyState.tryOtherKeywords') : t('emptyState.createFirst')}
            action={
              !searchQuery ? (
                <div className="flex gap-2">
                  <button
                    onClick={() => setShowInstallDialog(true)}
                    className="inline-flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 bg-white rounded-lg hover:bg-gray-50"
                  >
                    <CloudDownload className="w-5 h-5" />
                    {t('installSkill')}
                  </button>
                  <button
                    onClick={() => setShowCreateSheet(true)}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
                  >
                    <Plus className="w-5 h-5" />
                    {t('createSkill')}
                  </button>
                </div>
              ) : undefined
            }
          />
        ) : (
          <>
            {/* 用户自定义技能（source: user | flocks | claude | 其他） */}
            {userSkills.length > 0 && (
              <div className="mb-5">
                <div className="flex items-center gap-2 mb-2.5 px-1">
                  <FolderOpen className="w-4 h-4 text-slate-500" />
                  <span className="text-sm font-semibold text-slate-700">{t('section.custom')}</span>
                  <span className="text-xs text-slate-600 bg-slate-100 px-1.5 py-0.5 rounded-full">
                    {userSkills.length}
                  </span>
                </div>
                <div className="grid gap-3 grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {userSkills.map(skill => (
                    <SkillCard
                      key={skill.name}
                      skill={skill}
                      language={i18n.language}
                      isSelected={sheetSkill?.name === skill.name}
                      installingDeps={installingDeps[skill.name] ?? false}
                      onClick={() => handleSelectSkill(skill)}
                      onInstallDeps={(e) => handleInstallDeps(skill, e)}
                      onDelete={(e) => handleDeleteFromCard(skill, e)}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* 内置技能（source: project，即项目 .flocks/plugins 目录） */}
            {builtinSkills.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-2.5 px-1">
                  <Sparkles className="w-4 h-4 text-purple-500" />
                  <span className="text-sm font-semibold text-purple-700">{t('section.builtin')}</span>
                  <span className="text-xs text-purple-400 bg-purple-50 px-1.5 py-0.5 rounded-full">
                    {builtinSkills.length}
                  </span>
                </div>
                <div className="grid gap-3 grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {builtinSkills.map(skill => (
                    <SkillCard
                      key={skill.name}
                      skill={skill}
                      language={i18n.language}
                      isSelected={sheetSkill?.name === skill.name}
                      installingDeps={installingDeps[skill.name] ?? false}
                      onClick={() => handleSelectSkill(skill)}
                      onInstallDeps={(e) => handleInstallDeps(skill, e)}
                      onDelete={(e) => handleDeleteFromCard(skill, e)}
                    />
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Detail / Edit Sheet */}
      {sheetSkill && (
        <SkillSheet
          skill={sheetSkill}
          onClose={() => setSheetSkill(null)}
          onSaved={() => { fetchSkills(); setSheetSkill(null); }}
          onDeleted={() => { fetchSkills(); setSheetSkill(null); }}
        />
      )}

      {/* Create Sheet */}
      {showCreateSheet && (
        <SkillSheet
          onClose={() => setShowCreateSheet(false)}
          onSaved={() => { fetchSkills(); setShowCreateSheet(false); }}
        />
      )}

      {/* Install Dialog */}
      {showInstallDialog && (
        <SkillInstallDialog
          onClose={() => setShowInstallDialog(false)}
          onInstalled={handleInstalled}
        />
      )}
    </div>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Custom skills: everything except project-installed (.flocks/plugins) built-in skills. */
function isUserManaged(skill: Skill): boolean {
  return skill.source !== 'project';
}

// ─── SkillCard ────────────────────────────────────────────────────────────────

interface SkillCardProps {
  skill: Skill;
  language: string;
  isSelected: boolean;
  installingDeps: boolean;
  onClick: () => void;
  onInstallDeps: (e: React.MouseEvent) => void;
  onDelete?: (e: React.MouseEvent) => void;
}

// 内置技能的颜色配置（按 source 区分，同 source 内按 name 散列分色）
const BUILTIN_PALETTES = [
  { bg: 'bg-purple-50', border: 'border-purple-200', icon: 'bg-purple-100 text-purple-600', ring: 'ring-purple-300', name: 'text-purple-900' },
  { bg: 'bg-violet-50', border: 'border-violet-200', icon: 'bg-violet-100 text-violet-600', ring: 'ring-violet-300', name: 'text-violet-900' },
  { bg: 'bg-sky-50', border: 'border-sky-200', icon: 'bg-sky-100 text-sky-600', ring: 'ring-sky-300', name: 'text-sky-900' },
  { bg: 'bg-teal-50', border: 'border-teal-200', icon: 'bg-teal-100 text-teal-600', ring: 'ring-teal-300', name: 'text-teal-900' },
  { bg: 'bg-emerald-50', border: 'border-emerald-200', icon: 'bg-emerald-100 text-emerald-600', ring: 'ring-emerald-300', name: 'text-emerald-900' },
];

const PROJECT_PALETTE = {
  bg: 'bg-slate-50', border: 'border-slate-200', icon: 'bg-slate-100 text-slate-600', ring: 'ring-slate-300', name: 'text-slate-900',
};

function hashIndex(str: string, len: number) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return h % len;
}

function SkillCard({ skill, language, isSelected, installingDeps, onClick, onInstallDeps, onDelete }: SkillCardProps) {
  const { t } = useTranslation('skill');
  const isUserSkill = isUserManaged(skill) && skill.source !== 'project';
  const palette = isUserManaged(skill)
    ? PROJECT_PALETTE
    : BUILTIN_PALETTES[hashIndex(skill.name, BUILTIN_PALETTES.length)];
  const localizedDescription = getLocalizedSkillDescription(skill, language);

  const hasMissingDeps = skill.eligible === false && (skill.install_specs?.length ?? 0) > 0;

  return (
    <div
      onClick={onClick}
      className={`
        relative rounded-xl border overflow-hidden cursor-pointer
        h-[180px] flex flex-col
        transition-all duration-150
        ${palette.bg} ${palette.border}
        ${isSelected
          ? `shadow-md ring-2 ${palette.ring}`
          : 'shadow-sm hover:shadow-md hover:brightness-95'
        }
      `}
    >
      {/* 删除图标（右上角） */}
      <button
        type="button"
        onClick={onDelete}
        disabled={!isUserSkill}
        title={!isUserSkill ? t('sheet.deleteBuiltinTip') : t('sheet.delete')}
        className={`absolute top-2 right-2 z-10 p-1 rounded-md transition-colors
          ${isUserSkill
            ? 'text-red-400 hover:text-red-600 hover:bg-red-50'
            : 'text-gray-300 cursor-not-allowed'
          }`}
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>

      {/* 顶部：图标 + 名称 */}
      <div className="flex items-start gap-3 px-4 pt-4 pb-2 pr-8">
        <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${palette.icon}`}>
          {isUserManaged(skill)
            ? <FolderOpen className="w-4 h-4" />
            : <Sparkles className="w-4 h-4" />
          }
        </div>
        <div className="min-w-0 flex-1">
          <span className={`text-sm font-semibold leading-tight block truncate ${palette.name}`}>
            {skill.name}
          </span>
          {skill.source && (
            <span className="text-[10px] text-gray-400 font-mono">
              {skill.source}
            </span>
          )}
        </div>
      </div>

      {/* 描述 */}
      <p className="flex-1 px-4 min-h-0 text-xs text-gray-600 leading-relaxed line-clamp-3">
        {localizedDescription || t('common:empty.noDescription')}
      </p>

      {/* 底部：eligibility badge */}
      <div className="px-4 pb-3 pt-1 flex items-center justify-between">
        {skill.eligible === true && (
          <span className="inline-flex items-center gap-1 text-[10px] text-green-600 font-medium">
            <CheckCircle className="w-3 h-3" />
            {t('eligibility.ready')}
          </span>
        )}
        {skill.eligible === false && (
          <span className="inline-flex items-center gap-1 text-[10px] text-amber-600 font-medium">
            <AlertTriangle className="w-3 h-3" />
            {t('eligibility.missingDeps')}
          </span>
        )}
        {skill.eligible == null && <span />}

        {/* Install deps button (shown only when there are missing deps with install specs) */}
        {hasMissingDeps && (
          <button
            onClick={onInstallDeps}
            disabled={installingDeps}
            title={t('eligibility.installDeps')}
            className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full
                       bg-amber-100 text-amber-700 border border-amber-200
                       hover:bg-amber-200 transition-colors disabled:opacity-50"
          >
            {installingDeps
              ? <Loader2 className="w-3 h-3 animate-spin" />
              : <CloudDownload className="w-3 h-3" />
            }
            {installingDeps ? t('eligibility.installing') : t('eligibility.installDeps')}
          </button>
        )}
      </div>
    </div>
  );
}
