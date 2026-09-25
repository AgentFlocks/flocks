import { pageActionLabel } from '../pageActionLabel';
import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  IconBook,
  IconSearch,
  IconPlus,
  IconRefresh,
  IconSparkles,
  IconFolderOpen,
  IconCloudDownload,
  IconAlertTriangle,
  IconLoader2,
  IconPencil,
  IconChevronLeft,
  IconChevronRight,
  IconFilter,
  IconCircleCheck,
  IconCircleX,
} from '@tabler/icons-react';
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

export default function SkillPage() {
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
        <LoadingSpinner delayMs={180} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-danger mb-4">{error}</p>
          <button onClick={() => void refreshSkillsAndFetch({ silent: false, invalidateOnError: true, force: true })} className="px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover">
            {t('common:button.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <PageHeader tone="ai"
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<IconBook aria-hidden="true" className="w-8 h-8" />}
      />

      {/* Toolbar: 搜索 · 来源 chips · 刷新/安装/创建 */}
      <div className="px-4 py-2 border-b border-line flex flex-wrap items-center gap-3">
        {/* 搜索 */}
        <div className="relative w-full sm:w-64 min-w-0">
          <IconSearch aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-fg-muted pointer-events-none" />
          <input
            type="text"
            placeholder={t('searchPlaceholder')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="ui-input w-full pl-9 pr-3  border border-line rounded-control focus:border-line-control text-sm bg-surface"
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
            className={`ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border transition-all ${
              refreshDone
                ? 'border-success text-success'
                : 'border-line text-fg-muted hover:bg-hover hover:text-fg-secondary disabled:bg-disabled disabled:text-fg-disabled'
            }`}
          >
            <IconRefresh aria-hidden="true" className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => setShowInstallDialog(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-line-control text-fg-secondary bg-surface rounded-control hover:bg-hover transition-colors text-sm"
          >
            <IconCloudDownload aria-hidden="true" className="w-4 h-4" />
            {t('installSkill')}
          </button>
          <button
            onClick={() => setShowCreateSheet(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors text-sm"
          >
            <IconPlus aria-hidden="true" className="w-4 h-4" />
            {t('createSkill')}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {visibleSkills.length === 0 ? (
          // Truly empty inventory — show big EmptyState with CTAs
          <EmptyState
            icon={<IconBook aria-hidden="true" className="w-8 h-8" />}
            title={t('emptyState.noSkills')}
            description={t('emptyState.createFirst')}
            action={
              <div className="flex gap-2">
                <button
                  onClick={() => setShowInstallDialog(true)}
                  className="inline-flex items-center gap-2 px-4 py-2 border border-line-control text-fg-secondary bg-surface rounded-control hover:bg-hover"
                >
                  <IconCloudDownload aria-hidden="true" className="w-5 h-5" />
                  {t('installSkill')}
                </button>
                <button
                  onClick={() => setShowCreateSheet(true)}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover"
                >
                  <IconPlus aria-hidden="true" className="w-5 h-5" />
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
    <div className="bg-surface border border-line rounded-card overflow-x-auto">
      {/* Below md the table keeps only name / enabled / actions so it fits a phone
          without horizontal scrolling (the type badge moves into the name cell and
          the action buttons stay on screen). From md up the 640px floor keeps the
          five columns usable; widths live on the header cells because table-fixed
          reads them from the first row and a hidden <col> cannot be dropped. */}
      <table className="w-full table-fixed text-xs md:min-w-[640px]">
        <thead className="bg-subtle text-fg-muted border-b border-line">
          <tr>
            <th className="hidden md:table-cell md:w-[104px] text-left px-4 py-2.5 font-medium whitespace-nowrap">{t('table.type')}</th>
            <th className="text-left px-3 md:px-4 py-2.5 font-medium whitespace-nowrap">{t('table.name')}</th>
            <th className="hidden md:table-cell md:w-[18%] text-left px-4 py-2.5 font-medium">
              <ColumnFilterHeader
                label={t('table.source')}
                values={allSources}
                active={sourceColFilter}
                onToggle={onToggleSourceColFilter}
                onClear={onClearSourceColFilter}
              />
            </th>
            <th className="hidden md:table-cell md:w-[14%] text-left px-4 py-2.5 font-medium">
              <ColumnFilterHeader
                label={t('table.enabled')}
                values={['enabled', 'disabled']}
                active={enabledFilter}
                onToggle={onToggleEnabledFilter}
                onClear={onClearEnabledFilter}
                renderLabel={(v) => v === 'enabled' ? t('filter.enabled') : t('filter.disabled')}
              />
            </th>
            {/* phone: the enable switch (icon-only) joins the action buttons here */}
            <th className="w-[112px] md:w-[14%] text-right px-2 md:px-4 py-2.5 font-medium">{t('table.actions')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
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
                <div className="flex flex-col items-center justify-center gap-3 text-fg-muted">
                  <IconBook aria-hidden="true" className="w-8 h-8 text-fg-placeholder" />
                  <div className="text-sm">{t('emptyState.noResults')}</div>
                  {hasActiveFilter && (
                    <button
                      type="button"
                      onClick={onClearAllFilters}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-control border border-line text-xs text-fg-secondary hover:bg-hover hover:text-fg transition-colors"
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
        skill.disabled ? 'bg-subtle ' : 'hover:bg-hover '
      } ${isSelected ? 'bg-subtle ' : ''}`}
    >
      {/* 类型列（phone 宽度下并入名称列） */}
      <td className="hidden md:table-cell px-4 py-3">
        <SourceTypeBadge isUser={isUser} />
      </td>

      {/* 名称 + 描述列（含内嵌的缺依赖警告）— 整块作为打开 SkillSheet 的点击区，
          mirrors the Hub catalog row pattern (`Hub/index.tsx` HubTable). */}
      <td className="max-w-0 px-3 md:px-4 py-3">
        <button
          type="button"
          onClick={() => onSelect(skill)}
          title={t('table.edit')}
          className="flex items-start gap-2.5 min-w-0 w-full text-left group/name "
        >
          <span className="w-7 h-7 rounded-control flex items-center justify-center shrink-0 bg-subtle text-fg-muted mt-0.5">
            {isUser
              ? <IconFolderOpen aria-hidden="true" className="w-4 h-4" />
              : <IconSparkles aria-hidden="true" className="w-4 h-4" />
            }
          </span>
          <span className="min-w-0 flex-1">
            <span className="block font-medium text-fg truncate transition-colors group-hover/name:text-fg-secondary group-focus-visible/name:underline">
              {skill.name}
            </span>
            <span className="block text-xs text-fg-muted truncate mt-0.5">
              {skill.description || t('sheet.noDescription')}
            </span>
            <span className="md:hidden block mt-1 text-xs">
              <SourceTypeBadge isUser={isUser} />
            </span>
            {/* The old "Status" column collapsed to this single inline
                warning — only rendered when the skill is actually
                missing dependencies, so the row stays quiet by default. */}
            {hasMissingDeps && (
              <span
                className="text-xs text-warning truncate mt-0.5 flex items-center gap-1"
                title={(skill.missing ?? []).join(', ')}
              >
                <IconAlertTriangle aria-hidden="true" className="w-4 h-4 shrink-0" />
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

      {/* 来源列（phone 宽度下隐藏，详情在编辑抽屉里） */}
      <td className="hidden md:table-cell max-w-0 px-4 py-3">
        <span className="text-xs text-fg-muted font-mono truncate block" title={skill.location}>
          {skill.source ?? '-'}
        </span>
      </td>

      {/* 启用开关列：控制 skill 是否注入 Agent System Prompt（phone 宽度下并入操作列） */}
      <td className="hidden md:table-cell px-4 py-3">
        <SkillEnabledControl
          enabled={enabled}
          loading={toggling}
          title={enabled ? t('toggle.enabledTip') : t('toggle.disabledTip')}
          onChange={(e) => onToggle(skill, e)}
        />
      </td>

      {/* 操作列：编辑（+ 缺依赖时的安装按钮）；删除操作在编辑面板内 */}
      <td className="px-2 md:px-4 py-3 text-right">
        <div className="flex items-center justify-end gap-1">
          <span className="md:hidden inline-flex">
            <SkillEnabledControl
              enabled={enabled}
              loading={toggling}
              compact
              title={enabled ? t('toggle.enabledTip') : t('toggle.disabledTip')}
              onChange={(e) => onToggle(skill, e)}
            />
          </span>
          {hasMissingDeps && (
            <button
              onClick={(e) => onInstallDeps(skill, e)}
              disabled={installingDeps}
              title={t('eligibility.installDeps')}
              aria-label={t('eligibility.installDeps')}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-tag border border-line
 bg-surface text-fg-secondary hover:bg-hover transition-colors disabled:bg-disabled disabled:text-fg-disabled"
            >
              {installingDeps
                ? <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />
                : <IconCloudDownload aria-hidden="true" className="w-4 h-4" />
              }
              {/* label only from md up: on a phone the icon button keeps the column narrow */}
              <span className="hidden md:inline">{installingDeps ? t('eligibility.installing') : t('eligibility.installDeps')}</span>
            </button>
          )}
          <button
            type="button"
            onClick={() => onSelect(skill)}
            title={t('table.edit')}
            aria-label={t('table.edit')}
            className="ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border border-line text-fg-muted
 hover:border-line-control hover:text-fg-secondary hover:bg-hover
 transition-colors"
          >
            <IconPencil aria-hidden="true" className="w-4 h-4" />
          </button>
        </div>
      </td>
    </tr>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function SkillEnabledControl({ enabled, loading, title, onChange, compact = false }: {
  enabled: boolean;
  loading: boolean;
  title?: string;
  onChange: (e: React.MouseEvent) => void;
  /** Icon-only pill for phone widths, where the text pill does not fit next to the action buttons. */
  compact?: boolean;
}) {
  const { t } = useTranslation('common');
  return (
    <span className="inline-flex items-center gap-2">
      <button type="button" role="switch" aria-checked={enabled} aria-label={title || t('status.enabled')}
        onClick={onChange} disabled={loading} title={title} className="ui-btn-icon inline-flex shrink-0 items-center justify-center rounded-control disabled:cursor-not-allowed">
<span className="ui-switch" aria-hidden="true" aria-checked={enabled} aria-disabled={loading} />
</button>
      <span className={`${compact ? 'text-xs' : 'text-sm'} ${loading ? 'text-fg-disabled' : 'text-fg-secondary'}`}>
        {t(loading ? 'status.loading' : enabled ? 'status.enabled' : 'status.disabled')}
      </span>
    </span>
  );
}

function SourceTypeBadge({ isUser }: { isUser: boolean }) {
  const { t } = useTranslation('skill');
  if (isUser) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-tag border border-line bg-surface text-fg-muted whitespace-nowrap">
        <IconFolderOpen aria-hidden="true" className="w-4 h-4" />
        {t('table.custom')}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-tag border border-line bg-subtle text-fg-muted whitespace-nowrap">
      <IconSparkles aria-hidden="true" className="w-4 h-4" />
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
      className="inline-flex items-center rounded-control border border-line bg-surface p-0.5 text-xs"
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
            className={`px-2.5 py-1 rounded-control transition-colors whitespace-nowrap ${
              active
                ? 'bg-selected text-fg'
                : 'text-fg-muted hover:text-fg-secondary hover:bg-selected'
            } ${idx > 0 ? 'ml-0.5' : ''}`}
          >
            <span>{chip.label}</span>
            <span
              className={`ml-1.5 inline-block min-w-[1.25rem] px-1 rounded-tag text-xs tabular-nums ${
                active ? 'bg-surface' : 'bg-subtle text-fg-muted'
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
        <span className={hasFilter ? 'text-danger' : ''}>{label}</span>
        <button
          type="button"
          onClick={() => setOpen(v => !v)}
          className={`ui-btn-icon inline-flex items-center justify-center p-0.5 rounded-control hover:bg-hover transition-colors ${
            hasFilter ? 'text-danger' : 'text-fg-muted'
          }`}
          aria-label={`${label} ${t('filter.aria')}`}
        >
          <IconFilter aria-hidden="true" className="w-4 h-4" />
        </button>
      </div>
      {open && (
        <div className="absolute left-0 top-full mt-1 z-20 bg-surface rounded-control shadow-lg border border-line py-2 min-w-[160px] max-h-60 overflow-y-auto">
          {hasFilter && (
            <button
              onClick={() => { onClear(); setOpen(false); }}
              className="w-full text-left px-3 py-1.5 text-xs text-danger hover:bg-danger-soft"
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
                className="flex items-center px-3 py-1.5 hover:bg-hover cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggle(v)}
                  className="w-3.5 h-3.5 rounded-tag border-line-control text-danger mr-2"
                />
                <span className="text-xs text-fg-secondary">{displayLabel}</span>
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
    <div className="px-4 py-2 border-t border-line bg-surface flex items-center justify-between text-xs text-fg-muted">
      <span>{t('pagination.info', { start, end, total })}</span>
      <div className="flex items-center gap-1">
        <button aria-label={pageActionLabel('previous')}
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          className="ui-btn-icon inline-flex items-center justify-center p-1 rounded-control border border-line hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled"
        >
          <IconChevronLeft aria-hidden="true" className="w-4 h-4" />
        </button>
        <span className="min-w-16 text-center">{page} / {totalPages}</span>
        <button aria-label={pageActionLabel('next')}
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
          className="ui-btn-icon inline-flex items-center justify-center p-1 rounded-control border border-line hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled"
        >
          <IconChevronRight aria-hidden="true" className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
