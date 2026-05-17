import { useState, useEffect, useMemo, useRef } from 'react';
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
  const toast = useToast();

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

  const fetchSkills = async (
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
          toast.error(t('refreshFailed'), message);
        }
        return false;
      }
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    fetchSkills();
  }, []);

  // Skills visible to this page: everything except the internal "system"
  // category.  Counter chips operate on this set so the totals reflect
  // what the user can actually see — search and statusFilter both narrow
  // it further into ``filteredSkills``.
  const visibleSkills = useMemo(
    () => skills.filter(s => s.category !== 'system'),
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

  const handleRefresh = async () => {
    if (refreshing) return;
    try {
      setRefreshing(true);
      await Promise.all([
        skillAPI.refresh().then(() => fetchSkills({ silent: true })),
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

  const refreshSkillsAndFetch = async () => {
    try {
      await skillAPI.refresh();
    } catch {
      // Fall back to a plain fetch if refresh is temporarily unavailable.
    }
    return fetchSkills({ silent: true, invalidateOnError: true });
  };

  const handleSelectSkill = async (skill: Skill) => {
    try {
      const response = await skillAPI.get(skill.name);
      setSheetSkill(response.data);
    } catch (err: unknown) {
      toast.error(t('fetchDetailFailed'), err instanceof Error ? err.message : String(err));
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
      toast.error(t('toggle.failed'), err instanceof Error ? err.message : String(err));
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
        toast.success(t('eligibility.installSuccess'));
        await fetchSkills({ silent: true, invalidateOnError: true });
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
          <button onClick={() => fetchSkills()} className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">
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
      />

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
            await fetchSkills({ silent: true, invalidateOnError: true });
            setSheetSkill(null);
          }}
          onDeleted={async () => {
            await fetchSkills({ silent: true, invalidateOnError: true });
            setSheetSkill(null);
          }}
        />
      )}

      {showCreateSheet && (
        <SkillSheet
          onClose={() => setShowCreateSheet(false)}
          onSaved={async () => {
            setShowCreateSheet(false);
            await refreshSkillsAndFetch();
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

  // Row is no longer clickable — the edit button in the "Actions" column
  // is the only way to open SkillSheet.  We keep a subtle hover so users
  // can still scan rows visually, but drop ``cursor-pointer`` to avoid
  // promising a click-affordance that isn't there.
  return (
    <tr
      className={`transition-colors ${
        skill.disabled ? 'opacity-50' : 'hover:bg-gray-50'
      } ${isSelected ? 'bg-slate-50' : ''}`}
    >
      {/* 类型列 */}
      <td className="px-4 py-3">
        <SourceTypeBadge isUser={isUser} />
      </td>

      {/* 名称 + 描述列（含内嵌的缺依赖警告） */}
      <td className="max-w-0 px-4 py-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 bg-gray-100 text-gray-400 mt-0.5">
            {isUser
              ? <FolderOpen className="w-3.5 h-3.5" />
              : <Sparkles className="w-3.5 h-3.5" />
            }
          </div>
          <div className="min-w-0 flex-1">
            <div className="font-medium text-gray-900 truncate">{skill.name}</div>
            <div className="text-[11px] text-gray-400 truncate mt-0.5">
              {skill.description || t('sheet.noDescription')}
            </div>
            {/* The old "Status" column collapsed to this single inline
                warning — only rendered when the skill is actually
                missing dependencies, so the row stays quiet by default. */}
            {hasMissingDeps && (
              <div
                className="text-[11px] text-amber-600 truncate mt-0.5 flex items-center gap-1"
                title={(skill.missing ?? []).join(', ')}
              >
                <AlertTriangle className="w-3 h-3 shrink-0" />
                <span className="truncate">
                  {t('eligibility.missingDepsInline', {
                    list: (skill.missing ?? []).join(', '),
                  })}
                </span>
              </div>
            )}
          </div>
        </div>
      </td>

      {/* 来源列 */}
      <td className="max-w-0 px-4 py-3">
        <span className="text-[11px] text-gray-400 font-mono truncate block" title={skill.location}>
          {skill.source ?? '-'}
        </span>
      </td>

      {/* 启用开关列：控制 skill 是否注入 Agent System Prompt */}
      <td className="px-4 py-3">
        <ToggleSwitch
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

function ToggleSwitch({ enabled, loading, title, onChange }: {
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
      onClick={onChange}
      disabled={loading}
      title={title}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border-2 border-transparent
        transition-colors duration-150 focus:outline-none disabled:cursor-wait
        ${enabled ? 'bg-slate-700' : 'bg-gray-200'}`}
    >
      {loading
        ? <Loader2 className="absolute inset-0 m-auto w-3 h-3 text-white animate-spin" />
        : (
          <span className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow
            transform transition-transform duration-150
            ${enabled ? 'translate-x-4' : 'translate-x-0'}`}
          />
        )
      }
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
