import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  BookOpen,
  Search,
  Plus,
  RefreshCw,
  Sparkles,
  FolderOpen,
  CloudDownload,
  AlertTriangle,
  Loader2,
  Pencil,
  ChevronLeft,
  ChevronRight,
  Filter,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import { skillAPI, Skill } from '@/api/skill';
import { EnabledBadge } from '@/pages/Tool/components/badges';
import SkillSheet from './SkillSheet';
import SkillInstallDialog from './SkillInstallDialog';

const PAGE_SIZE = 25;

interface SkillPageProps {
  embedded?: boolean;
}

export default function SkillPage({ embedded = false }: SkillPageProps = {}) {
  const { t } = useTranslation('skill');
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [installingDeps, setInstallingDeps] = useState<Record<string, boolean>>({});
  const { error: showErrorToast, success: showSuccessToast } = useToast();

  const [sheetSkill, setSheetSkill] = useState<Skill | null>(null);
  const [showCreateSheet, setShowCreateSheet] = useState(false);
  const [showInstallDialog, setShowInstallDialog] = useState(false);
  const [page, setPage] = useState(1);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);
  const [togglingSkills, setTogglingSkills] = useState<Record<string, boolean>>({});
  // Toolbar: source filter (all / builtin / custom)
  const [sourceFilter, setSourceFilter] = useState<'all' | 'builtin' | 'custom'>('all');
  // Column-level multi-select filters (empty Set == "all").  Stored as
  // ``Set<string>`` so each chip can be toggled independently, matching
  // the Tool list page's column-filter behavior.
  const [enabledFilter, setEnabledFilter] = useState<Set<string>>(new Set());
  const [sourceColFilter, setSourceColFilter] = useState<Set<string>>(new Set());
  // Throttle anchor for `refreshSkillsAndFetch` — visibility/focus listeners
  // can fire several times in a single second; without this guard the page
  // would hammer the backend every time the tab gets focus.
  const lastRefreshRef = useRef(0);

  const fetchSkills = useCallback(async (
    { silent = false, invalidateOnError = false }: { silent?: boolean; invalidateOnError?: boolean } = {}
  ) => {
    try {
      if (!silent) {
        setLoading(true);
        setError(null);
      }
      const response = await skillAPI.status();
      setSkills(Array.isArray(response.data) ? response.data : []);
      setError(null);
      return true;
    } catch {
      try {
        const fallback = await skillAPI.list();
        setSkills(Array.isArray(fallback.data) ? fallback.data : []);
        setError(null);
        return true;
      } catch (listErr: unknown) {
        const message = listErr instanceof Error ? listErr.message : t('fetchListFailed');
        if (invalidateOnError || !silent) {
          setSkills([]);
          setError(message);
        }
        if (silent) {
          showErrorToast(t('refreshFailed'), message);
        }
        return false;
      }
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }, [showErrorToast, t]);

  useEffect(() => {
    void fetchSkills();
  }, [fetchSkills]);

  // Skills visible to this page: hide skills explicitly marked UI-hidden and
  // the legacy "system" category. Counter chips operate on this set so the
  // totals reflect what the user can actually see — search and statusFilter
  // both narrow it further into ``filteredSkills``.
  const visibleSkills = useMemo(
    () => skills.filter(s => !s.ui_hidden && s.category !== 'system'),
    [skills],
  );

  const enabledCount = useMemo(
    () => visibleSkills.filter(s => !s.disabled).length,
    [visibleSkills],
  );
  const disabledCount = visibleSkills.length - enabledCount;

  // All unique source values for column filter dropdown
  const allSources = useMemo(
    () => Array.from(new Set(visibleSkills.map(s => s.source ?? '-'))).sort(),
    [visibleSkills],
  );

  const filteredSkills = useMemo(() => {
    const q = searchQuery.toLowerCase();
    return visibleSkills.filter(skill => {
      const isUser = isUserManaged(skill);
      if (sourceFilter === 'builtin' && isUser) return false;
      if (sourceFilter === 'custom' && !isUser) return false;
      if (enabledFilter.size > 0) {
        const key = skill.disabled ? 'disabled' : 'enabled';
        if (!enabledFilter.has(key)) return false;
      }
      if (sourceColFilter.size > 0 && !sourceColFilter.has(skill.source ?? '-')) return false;
      if (!q) return true;
      return (
        skill.name.toLowerCase().includes(q) ||
        (skill.description || '').toLowerCase().includes(q)
      );
    });
  }, [visibleSkills, searchQuery, sourceFilter, enabledFilter, sourceColFilter]);

  const hasColumnFilter = enabledFilter.size > 0 || sourceColFilter.size > 0;

  // Reset to first page whenever any filter changes
  useEffect(() => { setPage(1); }, [searchQuery, sourceFilter, enabledFilter, sourceColFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredSkills.length / PAGE_SIZE));

  // Keep `page` state in sync with the list size.  Without this, deleting
  // enough items to shrink `totalPages` below the current `page` only
  // clamps the *view* via `currentPage` below — the underlying state stays
  // stale, so adding new skills later causes a confusing "jump" back to
  // the old page.  We mirror the clamp into state here.
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [totalPages, page]);

  const currentPage = Math.min(page, totalPages);
  const pagedSkills = useMemo(
    () => filteredSkills.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE),
    [filteredSkills, currentPage],
  );

  const refreshSkillsAndFetch = useCallback(async (
    {
      silent = true,
      invalidateOnError = true,
      force = false,
    }: {
      silent?: boolean;
      invalidateOnError?: boolean;
      force?: boolean;
    } = {}
  ) => {
    const now = Date.now();
    if (!force && now - lastRefreshRef.current < 1000) return true;
    lastRefreshRef.current = now;

    try {
      await skillAPI.refresh();
    } catch {
      // Fall back to a plain fetch if refresh is temporarily unavailable.
    }
    return fetchSkills({ silent, invalidateOnError });
  }, [fetchSkills]);

  const handleRefresh = async () => {
    if (refreshing) return;
    try {
      setRefreshing(true);
      // Ensure a minimum spin duration so the animation is clearly visible
      const [ok] = await Promise.all([
        refreshSkillsAndFetch({ silent: true, invalidateOnError: true, force: true }),
        new Promise((r) => setTimeout(r, 600)),
      ]);
      if (!ok) {
        showErrorToast(t('refreshFailed'), t('fetchListFailed'));
        return;
      }
      setRefreshDone(true);
      setTimeout(() => setRefreshDone(false), 2000);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void refreshSkillsAndFetch({ silent: true, invalidateOnError: false });
      }
    };

    const handleWindowFocus = () => {
      void refreshSkillsAndFetch({ silent: true, invalidateOnError: false });
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleWindowFocus);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleWindowFocus);
    };
  }, [refreshSkillsAndFetch]);

  const handleSelectSkill = async (skill: Skill) => {
    try {
      const response = await skillAPI.get(skill.name);
      setSheetSkill(response.data);
    } catch (err: unknown) {
      showErrorToast(t('fetchDetailFailed'), err instanceof Error ? err.message : String(err));
    }
  };

  const handleToggle = async (skill: Skill, e: React.MouseEvent) => {
    e.stopPropagation();
    setTogglingSkills(prev => ({ ...prev, [skill.name]: true }));
    try {
      const res = await skillAPI.toggle(skill.name);
      // Update local state after the server confirms — keeps UI in sync
      // without an extra GET round-trip.
      setSkills(prev => prev.map(s =>
        s.name === skill.name ? { ...s, disabled: res.data.disabled } : s
      ));
    } catch (err: unknown) {
      showErrorToast(t('toggle.failed'), err instanceof Error ? err.message : String(err));
    } finally {
      setTogglingSkills(prev => ({ ...prev, [skill.name]: false }));
    }
  };

  const handleInstallDeps = async (skill: Skill, e: React.MouseEvent) => {
    e.stopPropagation();
    setInstallingDeps(prev => ({ ...prev, [skill.name]: true }));
    try {
      const res = await skillAPI.installDeps(skill.name);
      const allOk = res.data.results.every(r => r.success);
      if (allOk) {
        showSuccessToast(t('eligibility.installSuccess'));
        await refreshSkillsAndFetch({ silent: true, invalidateOnError: true, force: true });
      } else {
        const errors = res.data.results
          .filter(r => !r.success)
          .map(r => r.error || 'unknown error')
          .join('; ');
        showErrorToast(t('eligibility.installFailed'), errors);
      }
    } catch (err: unknown) {
      showErrorToast(t('installDepsFailed'), err instanceof Error ? err.message : String(err));
    } finally {
      setInstallingDeps(prev => ({ ...prev, [skill.name]: false }));
    }
  };

  const handleInstalled = async () => {
    await refreshSkillsAndFetch();
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
          <button onClick={() => void refreshSkillsAndFetch({ silent: false, invalidateOnError: true, force: true })} className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">
            {t('common:button.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {!embedded && (
        <PageHeader
          title={t('pageTitle')}
          description={t('pageDescription')}
          icon={<BookOpen className="w-8 h-8" />}
        />
      )}

      {/* Toolbar: 搜索 · 来源 chips · 刷新/安装/创建 */}
      <div className="px-4 py-2 border-b border-gray-100 flex flex-wrap items-center gap-3">
        {/* 搜索 */}
        <div className="relative w-64">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
          <input
            type="text"
            placeholder={t('searchPlaceholder')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 border border-gray-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-slate-300 focus:border-slate-400 text-sm bg-white"
          />
        </div>

        {/* 来源筛选 chips */}
        <SourceFilterChips
          value={sourceFilter}
          onChange={setSourceFilter}
          total={visibleSkills.length}
          builtinCount={visibleSkills.filter(s => !isUserManaged(s)).length}
          customCount={visibleSkills.filter(s => isUserManaged(s)).length}
        />

        {/* 右侧操作：刷新 + 安装 + 创建 */}
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            title={refreshDone ? t('refreshed') : t('refresh')}
            className={`p-1.5 rounded-lg border transition-all ${
              refreshDone
                ? 'border-green-200 text-green-600'
                : 'border-gray-200 text-gray-400 hover:bg-gray-50 hover:text-gray-600 disabled:opacity-50'
            }`}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => setShowInstallDialog(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-gray-300 text-gray-700 bg-white rounded-lg hover:bg-gray-50 transition-colors text-sm"
          >
            <CloudDownload className="w-4 h-4" />
            {t('installSkill')}
          </button>
          <button
            onClick={() => setShowCreateSheet(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors text-sm"
          >
            <Plus className="w-4 h-4" />
            {t('createSkill')}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {visibleSkills.length === 0 ? (
          // Truly empty inventory — show big EmptyState with CTAs
          <EmptyState
            icon={<BookOpen className="w-16 h-16" />}
            title={t('emptyState.noSkills')}
            description={t('emptyState.createFirst')}
            action={
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
            }
          />
        ) : (
          // Always render the table (with header + filter dropdowns) when
          // the inventory has anything in it.  Empty filter result is shown
          // as a single empty row inside the table so the column headers
          // and filter funnels stay visible and operable.
          <SkillTable
            skills={pagedSkills}
            selectedSkill={sheetSkill}
            installingDeps={installingDeps}
            togglingSkills={togglingSkills}
            enabledFilter={enabledFilter}
            onToggleEnabledFilter={(v) =>
              setEnabledFilter(prev => {
                const next = new Set(prev);
                if (next.has(v)) next.delete(v); else next.add(v);
                return next;
              })
            }
            onClearEnabledFilter={() => setEnabledFilter(new Set())}
            allSources={allSources}
            sourceColFilter={sourceColFilter}
            onToggleSourceColFilter={(v) =>
              setSourceColFilter(prev => {
                const next = new Set(prev);
                if (next.has(v)) next.delete(v); else next.add(v);
                return next;
              })
            }
            onClearSourceColFilter={() => setSourceColFilter(new Set())}
            hasActiveFilter={sourceFilter !== 'all' || hasColumnFilter || !!searchQuery}
            onClearAllFilters={() => {
              setSourceFilter('all');
              setEnabledFilter(new Set());
              setSourceColFilter(new Set());
              setSearchQuery('');
            }}
            onSelect={handleSelectSkill}
            onInstallDeps={handleInstallDeps}
            onToggle={handleToggle}
          />
        )}
      </div>

      {filteredSkills.length > PAGE_SIZE && (
        <PaginationBar
          total={filteredSkills.length}
          page={currentPage}
          totalPages={totalPages}
          onPageChange={setPage}
        />
      )}

      {sheetSkill && (
        <SkillSheet
          skill={sheetSkill}
          onClose={() => setSheetSkill(null)}
          onSaved={async () => {
            await refreshSkillsAndFetch({ silent: true, invalidateOnError: true, force: true });
            setSheetSkill(null);
          }}
          onDeleted={async () => {
            await refreshSkillsAndFetch({ silent: true, invalidateOnError: true, force: true });
            setSheetSkill(null);
          }}
        />
      )}

      {showCreateSheet && (
        <SkillSheet
          onClose={() => {
            setShowCreateSheet(false);
            void refreshSkillsAndFetch({ silent: true, invalidateOnError: false, force: true });
          }}
          onSaved={async () => {
            setShowCreateSheet(false);
            await refreshSkillsAndFetch({ silent: true, invalidateOnError: true, force: true });
          }}
        />
      )}

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

function isUserManaged(skill: Skill): boolean {
  return skill.source !== 'project';
}

// ─── SkillTable ───────────────────────────────────────────────────────────────

interface SkillTableProps {
  skills: Skill[];
  selectedSkill: Skill | null;
  installingDeps: Record<string, boolean>;
  togglingSkills: Record<string, boolean>;
  enabledFilter: Set<string>;
  onToggleEnabledFilter: (v: string) => void;
  onClearEnabledFilter: () => void;
  allSources: string[];
  sourceColFilter: Set<string>;
  onToggleSourceColFilter: (v: string) => void;
  onClearSourceColFilter: () => void;
  hasActiveFilter: boolean;
  onClearAllFilters: () => void;
  onSelect: (skill: Skill) => void;
  onInstallDeps: (skill: Skill, e: React.MouseEvent) => void;
  onToggle: (skill: Skill, e: React.MouseEvent) => void;
}

function SkillTable({
  skills, selectedSkill, installingDeps, togglingSkills,
  enabledFilter, onToggleEnabledFilter, onClearEnabledFilter,
  allSources, sourceColFilter, onToggleSourceColFilter, onClearSourceColFilter,
  hasActiveFilter, onClearAllFilters,
  onSelect, onInstallDeps, onToggle,
}: SkillTableProps) {
  const { t } = useTranslation('skill');

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <table className="min-w-full table-fixed text-xs">
        <colgroup>
          <col style={{ width: '8%' }} />
          <col style={{ width: '46%' }} />
          <col style={{ width: '18%' }} />
          <col style={{ width: '14%' }} />
          <col style={{ width: '14%' }} />
        </colgroup>
        <thead className="bg-gray-50 text-gray-500 border-b border-gray-200">
          <tr>
            <th className="text-left px-4 py-2.5 font-medium">{t('table.type')}</th>
            <th className="text-left px-4 py-2.5 font-medium">{t('table.name')}</th>
            <th className="text-left px-4 py-2.5 font-medium">
              <ColumnFilterHeader
                label={t('table.source')}
                values={allSources}
                active={sourceColFilter}
                onToggle={onToggleSourceColFilter}
                onClear={onClearSourceColFilter}
              />
            </th>
            <th className="text-left px-4 py-2.5 font-medium">
              <ColumnFilterHeader
                label={t('table.enabled')}
                values={['enabled', 'disabled']}
                active={enabledFilter}
                onToggle={onToggleEnabledFilter}
                onClear={onClearEnabledFilter}
                renderLabel={(v) => v === 'enabled' ? t('filter.enabled') : t('filter.disabled')}
              />
            </th>
            <th className="text-right px-4 py-2.5 font-medium">{t('table.actions')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {skills.map(skill => (
            <SkillRow
              key={skill.name}
              skill={skill}
              isSelected={selectedSkill?.name === skill.name}
              installingDeps={installingDeps[skill.name] ?? false}
              toggling={togglingSkills[skill.name] ?? false}
              onSelect={onSelect}
              onInstallDeps={onInstallDeps}
              onToggle={onToggle}
            />
          ))}
          {skills.length === 0 && (
            <tr>
              <td colSpan={5} className="px-4 py-12">
                <div className="flex flex-col items-center justify-center gap-3 text-gray-400">
                  <BookOpen className="w-10 h-10 text-gray-300" />
                  <div className="text-sm">{t('emptyState.noResults')}</div>
                  {hasActiveFilter && (
                    <button
                      type="button"
                      onClick={onClearAllFilters}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-xs text-gray-600 hover:bg-gray-50 hover:text-gray-800 transition-colors"
                    >
                      {t('filter.clear')}
                    </button>
                  )}
                </div>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ─── SkillRow ─────────────────────────────────────────────────────────────────

interface SkillRowProps {
  skill: Skill;
  isSelected: boolean;
  installingDeps: boolean;
  toggling: boolean;
  onSelect: (skill: Skill) => void;
  onInstallDeps: (skill: Skill, e: React.MouseEvent) => void;
  onToggle: (skill: Skill, e: React.MouseEvent) => void;
}

function SkillRow({ skill, isSelected, installingDeps, toggling, onSelect, onInstallDeps, onToggle }: SkillRowProps) {
  const { t } = useTranslation('skill');
  const isUser = isUserManaged(skill);
  const hasMissingDeps = skill.eligible === false && (skill.install_specs?.length ?? 0) > 0;
  const enabled = !skill.disabled;

  // Row hover is purely a visual scan aid; the click affordance lives on the
  // name itself (mirrors the Hub catalog `<button>` pattern at
  // ``Hub/index.tsx`` line ~633) so the rest of the row — toggles, action
  // buttons — stays independently clickable without nested-button warnings.
  return (
    <tr
      className={`transition-colors ${
        skill.disabled ? 'bg-gray-50/40 dark:bg-zinc-900/30' : 'hover:bg-gray-50 dark:hover:bg-zinc-900/60'
      } ${isSelected ? 'bg-slate-50 dark:bg-zinc-900/80' : ''}`}
    >
      {/* 类型列 */}
      <td className="px-4 py-3">
        <SourceTypeBadge isUser={isUser} />
      </td>

      {/* 名称 + 描述列（含内嵌的缺依赖警告）— 整块作为打开 SkillSheet 的点击区，
          mirrors the Hub catalog row pattern (`Hub/index.tsx` HubTable). */}
      <td className="max-w-0 px-4 py-3">
        <button
          type="button"
          onClick={() => onSelect(skill)}
          title={t('table.edit')}
          className="flex items-start gap-2.5 min-w-0 w-full text-left group/name focus:outline-none"
        >
          <span className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 bg-gray-100 text-gray-400 mt-0.5">
            {isUser
              ? <FolderOpen className="w-3.5 h-3.5" />
              : <Sparkles className="w-3.5 h-3.5" />
            }
          </span>
          <span className="min-w-0 flex-1">
            <span className="block font-medium text-gray-900 truncate transition-colors group-hover/name:text-slate-700 group-focus-visible/name:underline">
              {skill.name}
            </span>
            <span className="block text-[11px] text-gray-400 truncate mt-0.5">
              {skill.description || t('sheet.noDescription')}
            </span>
            {/* The old "Status" column collapsed to this single inline
                warning — only rendered when the skill is actually
                missing dependencies, so the row stays quiet by default. */}
            {hasMissingDeps && (
              <span
                className="text-[11px] text-amber-600 truncate mt-0.5 flex items-center gap-1"
                title={(skill.missing ?? []).join(', ')}
              >
                <AlertTriangle className="w-3 h-3 shrink-0" />
                <span className="truncate">
                  {t('eligibility.missingDepsInline', {
                    list: (skill.missing ?? []).join(', '),
                  })}
                </span>
              </span>
            )}
          </span>
        </button>
      </td>

      {/* 来源列 */}
      <td className="max-w-0 px-4 py-3">
        <span className="text-[11px] text-gray-400 font-mono truncate block" title={skill.location}>
          {skill.source ?? '-'}
        </span>
      </td>

      {/* 启用开关列：控制 skill 是否注入 Agent System Prompt */}
      <td className="px-4 py-3">
        <SkillEnabledControl
          enabled={enabled}
          loading={toggling}
          title={enabled ? t('toggle.enabledTip') : t('toggle.disabledTip')}
          onChange={(e) => onToggle(skill, e)}
        />
      </td>

      {/* 操作列：编辑（+ 缺依赖时的安装按钮）；删除操作在编辑面板内 */}
      <td className="px-4 py-3 text-right">
        <div className="flex items-center justify-end gap-1">
          {hasMissingDeps && (
            <button
              onClick={(e) => onInstallDeps(skill, e)}
              disabled={installingDeps}
              title={t('eligibility.installDeps')}
              className="inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200
                         bg-white text-gray-600 hover:bg-gray-50 transition-colors disabled:opacity-50"
            >
              {installingDeps
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <CloudDownload className="w-3 h-3" />
              }
              {installingDeps ? t('eligibility.installing') : t('eligibility.installDeps')}
            </button>
          )}
          <button
            type="button"
            onClick={() => onSelect(skill)}
            title={t('table.edit')}
            aria-label={t('table.edit')}
            className="p-1.5 rounded-md border border-gray-200 text-gray-500
                       hover:border-slate-300 hover:text-slate-700 hover:bg-gray-50
                       transition-colors"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
        </div>
      </td>
    </tr>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function SkillEnabledControl({ enabled, loading, title, onChange }: {
  enabled: boolean;
  loading: boolean;
  title?: string;
  onChange: (e: React.MouseEvent) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-label={title}
      onClick={onChange}
      disabled={loading}
      title={title}
      className="relative inline-flex rounded-full transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-300 focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-75"
    >
      <EnabledBadge enabled={enabled} />
      {loading && (
        <span className="absolute inset-0 inline-flex items-center justify-center rounded-full bg-white/70 dark:bg-zinc-900/70">
          <Loader2 className="w-3 h-3 animate-spin text-gray-500 dark:text-zinc-300" />
        </span>
      )}
    </button>
  );
}

function SourceTypeBadge({ isUser }: { isUser: boolean }) {
  const { t } = useTranslation('skill');
  if (isUser) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-gray-200 bg-white text-gray-500 whitespace-nowrap">
        <FolderOpen className="w-3 h-3" />
        {t('table.custom')}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-gray-200 bg-gray-50 text-gray-500 whitespace-nowrap">
      <Sparkles className="w-3 h-3" />
      {t('table.builtin')}
    </span>
  );
}

// ─── SourceFilterChips ────────────────────────────────────────────────────────

type SourceFilterValue = 'all' | 'builtin' | 'custom';

function SourceFilterChips({
  value,
  onChange,
  total,
  builtinCount,
  customCount,
}: {
  value: SourceFilterValue;
  onChange: (v: SourceFilterValue) => void;
  total: number;
  builtinCount: number;
  customCount: number;
}) {
  const { t } = useTranslation('skill');
  const chips: Array<{ key: SourceFilterValue; label: string; count: number }> = [
    { key: 'all', label: t('filter.all'), count: total },
    { key: 'builtin', label: t('filter.builtin'), count: builtinCount },
    { key: 'custom', label: t('filter.custom'), count: customCount },
  ];
  return (
    <div
      role="tablist"
      aria-label={t('filter.aria')}
      className="inline-flex items-center rounded-lg border border-gray-200 bg-white p-0.5 text-xs"
    >
      {chips.map((chip, idx) => {
        const active = chip.key === value;
        return (
          <button
            key={chip.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(chip.key)}
            className={`px-2.5 py-1 rounded-md transition-colors whitespace-nowrap ${
              active
                ? 'bg-slate-700 text-white'
                : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
            } ${idx > 0 ? 'ml-0.5' : ''}`}
          >
            <span>{chip.label}</span>
            <span
              className={`ml-1.5 inline-block min-w-[1.25rem] px-1 rounded text-[10px] tabular-nums ${
                active ? 'bg-white/15' : 'bg-gray-100 text-gray-500'
              }`}
            >
              {chip.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ─── ColumnFilterHeader ───────────────────────────────────────────────────────
//
// Multi-select column-filter dropdown, visually matching the Tool list
// page's SortFilterHeader (label · funnel icon · checkbox menu · clear).
// Skill table doesn't need column-level sorting, so this is the
// filter-only variant.

function ColumnFilterHeader({
  label,
  values,
  active,
  onToggle,
  onClear,
  renderLabel,
}: {
  label: string;
  values: string[];
  active: Set<string>;
  onToggle: (v: string) => void;
  onClear: () => void;
  renderLabel?: (v: string) => string;
}) {
  const { t } = useTranslation('skill');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const hasFilter = active.size > 0;

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={ref} className="relative inline-flex items-center whitespace-nowrap">
      <div className="flex items-center gap-1">
        <span className={hasFilter ? 'text-red-600' : ''}>{label}</span>
        <button
          type="button"
          onClick={() => setOpen(v => !v)}
          className={`p-0.5 rounded hover:bg-gray-200 transition-colors ${
            hasFilter ? 'text-red-600' : 'text-gray-400'
          }`}
          aria-label={`${label} ${t('filter.aria')}`}
        >
          <Filter className="w-3 h-3" />
        </button>
      </div>
      {open && (
        <div className="absolute left-0 top-full mt-1 z-20 bg-white rounded-lg shadow-lg border border-gray-200 py-2 min-w-[160px] max-h-60 overflow-y-auto">
          {hasFilter && (
            <button
              onClick={() => { onClear(); setOpen(false); }}
              className="w-full text-left px-3 py-1.5 text-xs text-red-600 hover:bg-red-50"
            >
              {t('filter.clear')}
            </button>
          )}
          {values.map(v => {
            const checked = active.has(v);
            const displayLabel = renderLabel ? renderLabel(v) : v;
            return (
              <label
                key={v}
                className="flex items-center px-3 py-1.5 hover:bg-gray-50 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggle(v)}
                  className="w-3.5 h-3.5 rounded border-gray-300 text-red-600 focus:ring-red-500 mr-2"
                />
                <span className="text-xs text-gray-700">{displayLabel}</span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── PaginationBar ────────────────────────────────────────────────────────────

function PaginationBar({ total, page, totalPages, onPageChange }: {
  total: number;
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}) {
  const { t } = useTranslation('skill');
  const start = (page - 1) * PAGE_SIZE + 1;
  const end = Math.min(total, page * PAGE_SIZE);
  return (
    <div className="px-4 py-2 border-t border-gray-200 bg-white flex items-center justify-between text-xs text-gray-500">
      <span>{t('pagination.info', { start, end, total })}</span>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          className="p-1 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
        </button>
        <span className="min-w-16 text-center">{page} / {totalPages}</span>
        <button
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
          className="p-1 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40"
        >
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}
