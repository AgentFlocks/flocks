import PageDialog from '../PageDialog';
import { memo, useContext, useState, useEffect, useLayoutEffect, useMemo, useCallback, useRef, type RefObject } from 'react';
import {
  IconPlus,
  IconTrash,
  IconArchive,
  IconChevronDown,
  IconChevronLeft,
  IconChevronRight,
  IconSparkles,
  IconShield,
  IconSearch,
  IconAlertTriangle,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebar,
  IconRobot,
  IconLoader2,
  IconRouteSquare2 as WorkflowIcon,
  IconAdjustmentsHorizontal,
  IconSquareCheck,
  IconDots,
  IconEdit,
  IconDownload,
  IconShare,
  IconCpu,
  IconInfoCircle,
  IconX,
  IconCheck,
  IconFolderCode,
  IconFolderPlus,
  IconFolderOpen,
  IconCopy,
  IconArrowUp,
  IconServer,
  IconBook,
  IconHammer,
  IconClipboardList,
  IconTarget,
  IconUser,
  IconUsers,
} from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useSearchParams, useParams } from 'react-router-dom';
import SessionActionsMenu from './SessionActionsMenu';
import SessionComposerMenu, { SessionComposerMenuHeader, SessionModeOption } from './SessionComposerMenu';
import { sessionPath } from '@/utils/sessionUrl';
import { PaneActiveContext } from '@/components/layout/PaneActiveContext';
import CopyButton from '@/components/common/CopyButton';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { isNarrowViewport, useNarrowViewport } from '@/components/common/sidePanelSizing';
import ChannelIcon from '@/components/common/ChannelIcon';
import { useToast } from '@/components/common/Toast';
import SessionChat, { buildInstructionDisplayText, type PromptDisplayOptions, type SSEChatEvent, type SSEConnectionStatus } from '@/components/common/SessionChat';
import SessionContextPanel from './SessionContextPanel';
import { getInitialSidePanelWidth, getMaxSidePanelWidth, SIDE_PANEL_MIN_WIDTH } from '@/components/common/sidePanelSizing';
import { useSSE } from '@/hooks/useSSE';
import SuiteInstallProgressPanel, {
  applySuiteInstallProgressEvent,
  createSuiteInstallProgressState,
  failSuiteInstallProgress,
  type SuiteInstallProgressState,
} from '@/components/hub/SuiteInstallProgressPanel';
import { sessionApi, type SessionContextFile, type SessionContextSnapshot } from '@/api/session';
import {
  flocksproPolicyApi,
  isSessionExecutionSettingsUnsupported,
  type NetworkMode,
  type PermissionMode,
  type RuntimeMode,
} from '@/api/flocksproPolicy';
import { flocksproUsersApi } from '@/api/flocksproUsers';
import { hubAPI, type HubInstallProgressEvent } from '@/api/hub';
import { skillAPI, type Skill } from '@/api/skill';
import { workflowAPI, type WorkflowSummary } from '@/api/workflow';
import type { Agent } from '@/api/agent';
import { useSessions } from '@/hooks/useSessions';
import { useAgents } from '@/hooks/useAgents';
import { useProviders } from '@/hooks/useProviders';
import {
  useEnabledChatModelDefinitions,
  useResolvedDefaultModel,
} from '@/hooks/useChatModelResources';
import client, { getApiBase } from '@/api/client';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import { buildPromptParts, type FilePartData } from '@/utils/imageUpload';
import { getAgentDisplayDescription, getAgentDisplayName, isAgentUsableInChat } from '@/utils/agentDisplay';
import { formatRelativeTime, formatSessionDate } from '@/utils/time';
import { getWorkflowDisplayName } from '@/utils/workflowDisplay';
import { formatPricingPerMillion, isPricingFree } from '@/utils/modelPricing';
import type { Message, ModelDefinitionV2, Session } from '@/types';
import { createMessageId } from '@/utils/messageId';
import { extractErrorMessage } from '@/utils/error';
import { useAuth } from '@/contexts/AuthContext';
import {
  DEFAULT_SESSION_EXECUTION_MODE,
  promoteDraftExecutionMode,
  readSessionExecutionMode,
  resetDraftExecutionMode,
  writeSessionExecutionMode,
  type PersistentSessionExecutionMode,
  type SessionExecutionMode,
} from '@/utils/sessionExecutionMode';

function sanitizeSessionExportName(value: string) {
  const trimmed = value.trim();
  return trimmed
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'session';
}

function getSessionDisplayTitle(session: Pick<Session, 'title' | 'channelID'>): string {
  if (!session.channelID) return session.title;
  const prefix = `[${session.channelID}]`;
  if (session.title.slice(0, prefix.length).toLowerCase() !== prefix.toLowerCase()) {
    return session.title;
  }
  return session.title.slice(prefix.length).trim() || session.title;
}

function ChannelChatTypeBadge({
  chatType,
  label,
}: {
  chatType: NonNullable<Session['channelChatType']>;
  label: string;
}) {
  const Icon = chatType === 'direct' ? IconUser : IconUsers;
  return (
    <span
      role="img"
      aria-label={label}
      data-channel-chat-type={chatType}
      className={`absolute -bottom-1 -right-1 flex h-2.5 w-2.5 items-center justify-center rounded-pill bg-surface ring-1 ring-focus ${
        chatType === 'direct' ? 'text-fg-muted' : 'text-info'
      }`}
    >
      <Icon aria-hidden="true" className="h-4 w-4 stroke-[2.5]" />
    </span>
  );
}

const LAST_SELECTED_SESSION_STORAGE_KEY = 'flocks:last-selected-session';
const SESSION_PAGE_VISITED_STORAGE_KEY = 'flocks:sessions:visited';
const SOC_WORKSPACE_COMPONENT_ID = 'soc-workspace';
const INSTALLED_HUB_STATES = new Set(['installed', 'localOnly', 'updateAvailable']);
const SESSION_UPDATE_REFETCH_DEBOUNCE_MS = 500;
const RECENT_SEARCH_SESSION_LIMIT = 5;
const AUTO_MODEL_KEY = '__flocks_auto__';
const TASK_SESSION_GROUP_ID = 'tasks';
const SESSION_EXECUTION_MODES: SessionExecutionMode[] = ['build', 'plan', 'goal'];
type AgentSourceFilter = 'all' | 'builtin' | 'custom';

function ExecutionModeIcon({
  mode,
  className = 'h-3 w-3',
}: {
  mode: SessionExecutionMode;
  className?: string;
}) {
  if (mode === 'plan') return <IconClipboardList aria-hidden="true" className={className} />;
  if (mode === 'goal') return <IconTarget aria-hidden="true" className={className} />;
  return <IconHammer aria-hidden="true" className={className} />;
}
type ProjectSummary = {
  id: string;
  worktree: string;
  vcs?: string | null;
  name?: string | null;
  isDefault?: boolean;
  pathStatus?: 'available' | 'missing' | 'unreadable';
  sessionCount?: number;
  matchedSessionCount?: number;
  lastActivityAt?: number | null;
  ownerUserID?: string | null;
  canWrite?: boolean;
  canDelete?: boolean;
  isShared?: boolean;
};
type ProjectDialogMode = 'create' | 'rename';
type ProjectSessionGroup = {
  id: string;
  label: string;
  worktree: string;
  sessions: Session[];
  sessionCount: number;
  pathStatus: 'available' | 'missing' | 'unreadable';
  canWrite: boolean;
  canDelete: boolean;
  isShared: boolean;
};
type FolderEntry = { name: string; path: string };
type FolderBrowserResponse = {
  path: string;
  parent?: string | null;
  roots: FolderEntry[];
  entries: FolderEntry[];
};

type ComposerResourceItem = {
  id: string;
  name: string;
  description?: string;
  badge?: string;
  source: 'builtin' | 'custom';
};

function ComposerResourcePicker({
  label,
  title,
  emptyText,
  icon: Icon,
  items,
  loading,
  open,
  onToggle,
  onSelect,
}: {
  label: string;
  title: string;
  emptyText: string;
  icon: React.ComponentType<{ className?: string }>;
  items: ComposerResourceItem[];
  loading: boolean;
  open: boolean;
  onToggle: () => void;
  onSelect: (item: ComposerResourceItem) => void;
}) {
  const { t } = useTranslation('session');
  const [sourceFilter, setSourceFilter] = useState<AgentSourceFilter>('all');
  const filteredItems = items.filter((item) => (
    sourceFilter === 'all' || item.source === sourceFilter
  ));

  return (
    <div className="relative">
      <button
        type="button"
        onClick={onToggle}
        className="group flex h-10 w-full items-center gap-2.5 rounded-card px-2 text-left text-xs font-medium text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="grid h-7 w-7 shrink-0 place-items-center text-fg-muted transition-colors group-hover:text-fg ">
          <Icon aria-hidden="true" className="h-4 w-4" />
        </span>
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <IconChevronRight aria-hidden="true" className={`h-4 w-4 shrink-0 text-fg-muted transition-transform ${open ? '-rotate-90' : ''}`} />
      </button>
      {open && (
        <div className="absolute bottom-0 left-[calc(100%+0.5rem)] z-[60] w-[300px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-card border border-line bg-surface shadow-overlay backdrop-blur-xl ">
          <div className="flex min-h-12 items-center justify-between gap-3 border-b border-line px-3 py-2 ">
            <span className="min-w-0 truncate text-xs font-semibold text-fg ">
              {title}
            </span>
            <div className="inline-flex shrink-0 items-center rounded-control bg-subtle p-0.5 text-xs ">
              {(['all', 'builtin', 'custom'] as AgentSourceFilter[]).map((filter) => (
                <button
                  key={filter}
                  type="button"
                  onClick={() => setSourceFilter(filter)}
                  className={`rounded-control px-2 py-1 transition-colors ${
                    sourceFilter === filter
                      ? 'bg-surface text-fg shadow-overlay '
                      : 'text-fg-muted hover:text-fg '
                  }`}
                >
                  {t(`agentPicker.filter.${filter}`)}
                </button>
              ))}
            </div>
          </div>
          <div className="h-64 space-y-0.5 overflow-y-auto p-1.5">
            {loading ? (
              <div className="flex h-full items-center justify-center text-fg-muted">
                <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
              </div>
            ) : filteredItems.length > 0 ? (
              filteredItems.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onSelect(item)}
                  title={item.description}
                  className="flex w-full min-w-0 items-center gap-2 rounded-control px-2.5 py-2 text-left text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
                >
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-control bg-subtle text-fg-muted ">
                    <Icon aria-hidden="true" className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs font-medium">{item.name}</span>
                  <span className={`shrink-0 rounded-tag px-1.5 py-0.5 text-xs font-medium ${
                    item.source === 'builtin'
                      ? 'bg-subtle text-fg-secondary '
                      : 'bg-data-soft text-data '
                  }`}>
                    {t(`agentPicker.badge.${item.source}`)}
                  </span>
                  {item.badge && (
                    <span className="max-w-20 shrink-0 truncate rounded-tag bg-subtle px-1.5 py-0.5 text-xs font-medium text-fg-muted ">
                      {item.badge}
                    </span>
                  )}
                </button>
              ))
            ) : (
              <div className="flex h-full items-center justify-center px-4 text-center text-xs text-fg-muted">
                {emptyText}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
const MULTI_PROJECT_SESSION_PAGE_SIZE = 6;
const SINGLE_PROJECT_SESSION_PAGE_SIZE = 20;

function readSessionStatusType(status: unknown): string | undefined {
  if (typeof status === 'string') return status;
  if (!status || typeof status !== 'object' || !('type' in status)) return undefined;
  return typeof status.type === 'string' ? status.type : undefined;
}

function isRunningSessionStatus(status: unknown): boolean {
  const statusType = readSessionStatusType(status);
  return statusType === 'busy'
    || statusType === 'compacting'
    || statusType === 'dreaming'
    || statusType === 'retry';
}

function readRunningSessionIds(statuses: unknown): Set<string> {
  if (!statuses || typeof statuses !== 'object' || Array.isArray(statuses)) return new Set();
  return new Set(
    Object.entries(statuses)
      .filter(([, status]) => isRunningSessionStatus(status))
      .map(([sessionId]) => sessionId),
  );
}
type ChatModelOption = {
  key: string;
  providerID: string;
  providerName: string;
  modelID: string;
  label: string;
  pricingLabel: string;
  contextLabel: string;
  contextWindowTokens: number | null;
  supportsVision: boolean | null;
};
type ChatModelProviderGroup = {
  providerID: string;
  providerName: string;
  models: ChatModelOption[];
};
type SelectorTooltip = {
  title: string;
  lines: string[];
  x: number;
  y: number;
};

function readLastSelectedSessionId(): string | null {
  try {
    return window.localStorage.getItem(LAST_SELECTED_SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeLastSelectedSessionId(sessionId: string | null) {
  try {
    if (sessionId) {
      window.localStorage.setItem(LAST_SELECTED_SESSION_STORAGE_KEY, sessionId);
    } else {
      window.localStorage.removeItem(LAST_SELECTED_SESSION_STORAGE_KEY);
    }
  } catch {
    // Ignore storage failures so the main chat flow is never blocked.
  }
}

function hasVisitedSessionPage(): boolean {
  try {
    return window.sessionStorage.getItem(SESSION_PAGE_VISITED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function markSessionPageVisited() {
  try {
    window.sessionStorage.setItem(SESSION_PAGE_VISITED_STORAGE_KEY, 'true');
  } catch {
    // Ignore storage failures; this only controls best-effort restore behavior.
  }
}

function shouldSkipLastSelectedSessionRestore(state: unknown): boolean {
  return Boolean(
    state
      && typeof state === 'object'
      && 'skipLastSelectedSessionRestore' in state
      && (state as { skipLastSelectedSessionRestore?: unknown }).skipLastSelectedSessionRestore,
  );
}

function makeModelKey(providerID: string, modelID: string): string {
  return `${providerID}::${modelID}`;
}

function getPathBasename(path: string): string {
  const normalized = path.replace(/[\\/]+$/g, '');
  const parts = normalized.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path || 'Project';
}

function getProjectLabel(project?: ProjectSummary, fallbackDirectory?: string): string {
  const explicitName = project?.name?.trim();
  if (explicitName) return explicitName;
  const directory = project?.worktree || fallbackDirectory || '';
  return getPathBasename(directory);
}

interface SessionSidebarItemProps {
  session: Session;
  nested?: boolean;
  selected: boolean;
  selectMode: boolean;
  checked: boolean;
  menuOpen: boolean;
  running: boolean;
  renaming: boolean;
  renameValue: string;
  renameSubmitting: boolean;
  language: string;
  relativeTimeClock: number;
  t: (key: string, options?: Record<string, unknown>) => string;
  renameInputRef: RefObject<HTMLInputElement | null>;
  onSelect: (sessionId: string) => void;
  onToggleCheck: (sessionId: string) => void;
  onRenameValueChange: (value: string) => void;
  onSubmitRename: (sessionId: string) => void | Promise<void>;
  onCancelRename: () => void;
  onToggleMenu: (sessionId: string, trigger: HTMLElement) => void;
}

function SessionSidebarItemInner({
  session,
  nested = false,
  selected,
  selectMode,
  checked,
  menuOpen,
  running,
  renaming,
  renameValue,
  renameSubmitting,
  language,
  t,
  renameInputRef,
  onSelect,
  onToggleCheck,
  onRenameValueChange,
  onSubmitRename,
  onCancelRename,
  onToggleMenu,
}: SessionSidebarItemProps) {
  const channelChatLabel = !session.channelChatType
    ? session.channelID
    : session.channelChatType === 'direct'
      ? t('channelDirectChat')
      : t('channelGroupChat');

  return (
    <div
      onClick={() => onSelect(session.id)}
      className={`group relative mb-0.5 min-h-[34px] cursor-pointer rounded-control border px-3 py-1 transition-colors duration-100 ${
        nested ? 'ml-5' : ''
      } ${
        !selectMode && selected
          ? 'border-transparent bg-selected text-fg '
          : selectMode && checked
          ? 'border-info bg-info-soft text-fg '
          : 'border-transparent text-fg-secondary hover:bg-hover hover:text-fg '
      }`}
    >
      <div className="flex min-w-0 items-center gap-1.5 pr-6">
        {selectMode && (
          <input
            type="checkbox"
            checked={checked}
            onChange={() => onToggleCheck(session.id)}
            onClick={(e) => e.stopPropagation()}
            className="flex-shrink-0 w-3.5 h-3.5 accent-info cursor-pointer rounded-tag"
          />
        )}
        {session.channelID && (
          <span
            title={channelChatLabel}
            className="relative inline-flex h-3.5 w-3.5 flex-shrink-0"
          >
            <ChannelIcon channelId={session.channelID} size="xs" />
            {session.channelChatType && (
              <ChannelChatTypeBadge
                chatType={session.channelChatType}
                label={channelChatLabel || session.channelID}
              />
            )}
          </span>
        )}
        {session.category === 'workflow' && (
          <span title={t('workflowSession')} className="flex-shrink-0">
            <WorkflowIcon aria-hidden="true" className="h-4 w-4 text-ai" />
          </span>
        )}
        {session.category === 'entity-config' && (
          <span title={t('configSession')} className="flex-shrink-0">
            <IconAdjustmentsHorizontal aria-hidden="true" className="h-4 w-4 text-ai" />
          </span>
        )}
        {renaming ? (
          <input
            ref={renameInputRef}
            value={renameValue}
            onChange={(e) => onRenameValueChange(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={() => void onSubmitRename(session.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); void onSubmitRename(session.id); }
              if (e.key === 'Escape') { e.preventDefault(); onCancelRename(); }
            }}
            placeholder={t('renamePlaceholder')}
            disabled={renameSubmitting}
            className="ui-input h-6 w-full min-w-0 rounded-control border border-info bg-surface px-1.5 text-sm text-fg focus:border-action"
            aria-label={t('rename')}
            data-session-rename-input
          />
        ) : (
          <h3 className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-sm font-medium">
            <span className="truncate">{getSessionDisplayTitle(session)}</span>
            {session.isShared && (
              <span className="inline-flex shrink-0 items-center rounded-pill border border-info bg-info-soft px-1.5 py-0.5 text-xs font-medium text-info ">
                {t('sharedTag')}
              </span>
            )}
          </h3>
        )}
        {session.time?.updated && !renaming && (
          <time
            dateTime={new Date(session.time.updated).toISOString()}
            title={formatSessionDate(session.time.updated)}
            className="ml-1 shrink-0 whitespace-nowrap text-xs font-normal tabular-nums text-fg-muted "
          >
            {formatRelativeTime(session.time.updated, language)}
          </time>
        )}
      </div>
      {!selectMode && (
        <div className="absolute right-1 top-1/2 h-6 w-6 -translate-y-1/2" data-session-actions>
          {running && (
            <span
              role="status"
              aria-label={t('chat.tool.running')}
              title={t('chat.tool.running')}
              data-session-running={session.id}
              className={`pointer-events-none absolute inset-0 grid place-items-center text-info transition-opacity ${
                menuOpen
                  ? 'opacity-0'
                  : 'opacity-100 group-hover:opacity-0 group-focus-within:opacity-0'
              }`}
            >
              <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            </span>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleMenu(session.id, e.currentTarget);
            }}
            title={t('moreActions')}
            aria-label={t('moreActions')}
            aria-expanded={menuOpen}
            className={`ui-btn-icon inline-flex items-center justify-center absolute inset-0 grid   place-items-center rounded-control text-fg-placeholder transition-[color,opacity,background-color] hover:bg-hover hover:text-fg ${
              menuOpen || (selected && !running)
                ? 'text-fg opacity-100 '
                : 'opacity-0 group-hover:opacity-100'
            }`}
          >
            <IconDots aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}

const SessionSidebarItem = memo(SessionSidebarItemInner, (prev, next) => (
  prev.session.id === next.session.id &&
  prev.nested === next.nested &&
  prev.session.title === next.session.title &&
  prev.session.category === next.session.category &&
  prev.session.channelID === next.session.channelID &&
  prev.session.channelChatType === next.session.channelChatType &&
  prev.session.isShared === next.session.isShared &&
  prev.session.time?.updated === next.session.time?.updated &&
  prev.selected === next.selected &&
  prev.selectMode === next.selectMode &&
  prev.checked === next.checked &&
  prev.menuOpen === next.menuOpen &&
  prev.running === next.running &&
  prev.renaming === next.renaming &&
  prev.renameValue === next.renameValue &&
  prev.renameSubmitting === next.renameSubmitting &&
  prev.language === next.language &&
  prev.relativeTimeClock === next.relativeTimeClock &&
  prev.t === next.t
));

/**
 * Live-connection indicator for the chat header. Only the two states a user
 * can act on are shown: reconnecting (transient) and failed (retries exhausted,
 * reload to resume). ``connected`` renders nothing.
 */
function SSEStatusIndicator({ status, t }: { status: SSEConnectionStatus; t: (key: string) => string }) {
  if (status !== 'reconnecting' && status !== 'failed') return null;
  const failed = status === 'failed';
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="sse-status"
      data-sse-status={status}
      className={`flex min-w-0 items-center gap-2 rounded-pill border px-2.5 py-1 text-xs font-medium ${
        failed
          ? 'border-danger bg-danger-soft text-danger '
          : 'border-warning bg-warning-soft text-warning '
      }`}
    >
      <span className={`h-1.5 w-1.5 shrink-0 rounded-pill ${failed ? 'bg-danger' : 'animate-pulse bg-warning'}`} />
      <span className="truncate">{failed ? t('connectionFailed') : t('reconnecting')}</span>
      {failed && (
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-tag px-1.5 py-0.5 text-xs font-medium underline-offset-2 hover:underline"
        >
          {t('chat.retry')}
        </button>
      )}
    </div>
  );
}

function WorkbenchRefreshStatus({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="workbench-refresh-status"
      className="ml-auto flex min-w-0 items-center gap-2 rounded-pill border border-line bg-surface px-2.5 py-1 text-xs font-medium text-fg-muted shadow-overlay "
    >
      <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-pill bg-info" />
      <span className="truncate">{label}</span>
      <span className="h-px w-8 shrink-0 overflow-hidden rounded-pill bg-info-soft ">
        <span className="block h-full w-1/2 animate-pulse rounded-pill bg-info" />
      </span>
    </div>
  );
}

function SessionListSkeleton() {
  return (
    <div
      data-testid="session-list-skeleton"
      aria-hidden="true"
      className="animate-pulse px-2 pt-1"
    >
      <div className="mb-2 h-3 w-16 rounded-pill bg-hover " />
      <div className="space-y-1.5">
        {[72, 88, 64].map((width, index) => (
          <div
            key={`${width}-${index}`}
            className="flex h-[34px] items-center gap-2 rounded-control px-2"
          >
            <div className="h-3.5 w-3.5 shrink-0 rounded-tag bg-hover " />
            <div
              className="h-3 rounded-pill bg-hover "
              style={{ width: `${width}%` }}
            />
          </div>
        ))}
      </div>
      <div className="mb-2 mt-5 h-3 w-12 rounded-pill bg-hover " />
      <div className="space-y-1.5">
        {[82, 68, 76, 58].map((width, index) => (
          <div
            key={`${width}-${index}`}
            className="flex h-[34px] items-center gap-2 rounded-control px-2"
          >
            <div className="h-3.5 w-3.5 shrink-0 rounded-pill bg-hover " />
            <div
              className="h-3 rounded-pill bg-hover "
              style={{ width: `${width}%` }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function SessionChatSkeleton() {
  return (
    <div
      data-testid="session-chat-skeleton"
      aria-hidden="true"
      className="flex min-h-0 flex-1 justify-center overflow-hidden px-7 pt-16"
    >
      <LoadingSpinner
        size="sm"
        delayMs={180}
        className="text-fg-muted [&_svg]:text-fg-muted "
      />
    </div>
  );
}

function mergeSessionContext(
  current: SessionContextSnapshot | null,
  incoming: SessionContextSnapshot,
  older: boolean,
): SessionContextSnapshot {
  const newest = older && current ? current : incoming;
  const previous = older ? incoming : current;
  const incomingIDs = new Set(incoming.messageIDs);
  const incomingFiles = [...incoming.outputs, ...incoming.contextFiles];
  // A fetched page replaces descriptors for its messages, including deletions.
  // A complete head is authoritative for the whole Session, even when empty.
  const retainedFiles = current && (older || incoming.hasMore)
    ? [...current.outputs, ...current.contextFiles].filter((file) => !incomingIDs.has(file.sourceMessageID))
    : [];
  const filesByKey = new Map<string, SessionContextFile>();
  for (const file of (older ? [...retainedFiles, ...incomingFiles] : [...incomingFiles, ...retainedFiles])) {
    const key = file.fileKey || file.resourceID;
    const existing = filesByKey.get(key);
    // A page filling a gap can be newer than retained historical descriptors.
    if (!existing || (
      typeof file.createdAt === 'number' && typeof existing.createdAt === 'number'
      && file.createdAt > existing.createdAt
    )) filesByKey.set(key, file);
  }
  const files = [...filesByKey.values()];
  const outputs = files.filter((file) => file.section === 'outputs');
  const contextFiles = files.filter((file) => file.section === 'context');
  const progressSource = (newest.progressKnown ?? newest.progress.length > 0) ? newest : previous;
  const progress = progressSource?.progress ?? [];
  const progressKnown = progressSource?.progressKnown ?? progress.length > 0;
  const progressCount = Number(progress.length > 0);
  const skills = [...new Map([
    ...(previous?.skills ?? []).map((skill) => [skill.name, skill] as const),
    ...newest.skills.map((skill) => [skill.name, skill] as const),
  ]).values()];
  // Retain the old boundary only for an overlapping partial head; otherwise
  // use the fetched page's boundary to fill gaps or finish a complete refresh.
  const overlapsHead = current?.messageIDs?.some((id) => incomingIDs.has(id));
  const pagination = older || !current || !incoming.hasMore || !overlapsHead ? incoming : current;
  return {
    ...newest,
    outputs,
    contextFiles,
    progress,
    progressKnown,
    skills,
    hasMore: pagination.hasMore,
    nextBefore: pagination.nextBefore ?? null,
    counts: {
      total: outputs.length + contextFiles.length + newest.roots.length + progressCount,
      outputs: outputs.length,
      contextFiles: contextFiles.length,
      roots: newest.roots.length,
      progress: progressCount,
    },
  };
}

export default function SessionPage() {
  const { t, i18n } = useTranslation('session');
  const { user } = useAuth();
  const activeProjectStorageKey = `flocks:sessions:active-project:${user?.id ?? 'anonymous'}`;
  const collapsedProjectsStorageKey = `flocks:sessions:collapsed-projects:${user?.id ?? 'anonymous'}`;
  const projectsSectionCollapsedStorageKey = `flocks:sessions:projects-section-collapsed:${user?.id ?? 'anonymous'}`;
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { sessionId: routeSessionId } = useParams();
  const selectedSessionId = routeSessionId || searchParams.get('session') || null;
  const navigationRef = useRef({ key: location.key, sessionId: selectedSessionId });
  navigationRef.current = { key: location.key, sessionId: selectedSessionId };
  const mountedRef = useRef(false);
  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      // Once this page leaves the route, its pending mutations may finish,
      // but must not navigate or overwrite the next page's selection.
      mountedRef.current = false;
    };
  }, []);
  // Inside the layout this page lives in a keep-alive pane: switching tabs
  // hides it instead of unmounting it, and its location stays pinned, so the
  // unmount / location-key guards above never fire. A hidden pane must not
  // navigate either, or a finishing create/archive would drag the whole app
  // back to the session it belongs to.
  const paneActive = useContext(PaneActiveContext);
  const paneActiveRef = useRef(paneActive);
  paneActiveRef.current = paneActive;
  const restoreAttemptRef = useRef<string | null>(null);
  const legacyNavigationRef = useRef<string | null>(null);
  const previousActionSessionRef = useRef(selectedSessionId);
  const [sessionLoadError, setSessionLoadError] = useState<{ sessionId: string; kind: 'unavailable' | 'forbidden' | 'failed' } | null>(null);
  const [sessionLoadAttempt, setSessionLoadAttempt] = useState(0);
  const [pendingInitialSessionId, setPendingInitialSessionId] = useState<string | null>(null);
  const selectSession = useCallback((id: string | null, replace = false) => {
    if (!mountedRef.current || !paneActiveRef.current) return;
    if (!id) writeLastSelectedSessionId(null);
    if (id && navigationRef.current.sessionId === id) return;
    navigate(id ? sessionPath(id) : '/sessions', {
      replace,
      state: id ? null : { skipLastSelectedSessionRestore: true },
    });
  }, [navigate]);
  const legacyNavigationPending = Boolean(selectedSessionId) && (searchParams.has('session') || searchParams.has('message') || searchParams.has('display'));
  const [pendingFocusMessageId, setPendingFocusMessageId] = useState<string | null>(null);
  // On phone widths (< 768px) the 282px task list would leave the chat pane
  // ~90px wide, so it starts collapsed there and, when opened, overlays the
  // chat instead of sharing the row with it.
  const narrowViewport = useNarrowViewport();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => isNarrowViewport());
  useEffect(() => {
    if (narrowViewport) setSidebarCollapsed(true);
  }, [narrowViewport]);
  const [selectedAgent, setSelectedAgent] = useState('rex');
  const [showAgentOptions, setShowAgentOptions] = useState(false);
  const [showWorkflowOptions, setShowWorkflowOptions] = useState(false);
  const [showSkillOptions, setShowSkillOptions] = useState(false);
  const [composerWorkflows, setComposerWorkflows] = useState<WorkflowSummary[]>([]);
  const [composerSkills, setComposerSkills] = useState<Skill[]>([]);
  const [loadingComposerResources, setLoadingComposerResources] = useState(false);
  const [selectedExecutionMode, setSelectedExecutionMode] = useState<SessionExecutionMode>(
    () => readSessionExecutionMode(null),
  );
  const [showExecutionModeOptions, setShowExecutionModeOptions] = useState(false);
  const executionModeSelectorRef = useRef<HTMLDivElement>(null);
  const executionModeHandoffRef = useRef<{
    sessionId: string;
    mode: SessionExecutionMode;
  } | null>(null);
  const [showProjectOptions, setShowProjectOptions] = useState(false);
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [showModelOptions, setShowModelOptions] = useState(false);
  const modelSelectorRef = useRef<HTMLDivElement>(null);
  const [showPermissionModeOptions, setShowPermissionModeOptions] = useState(false);
  const permissionModeSelectorRef = useRef<HTMLDivElement>(null);
  const [proPolicyEnabled, setProPolicyEnabled] = useState(false);
  const [sessionPermissionMode, setSessionPermissionMode] = useState<PermissionMode | null>(null);
  const [draftPermissionMode, setDraftPermissionMode] = useState<PermissionMode>('require-confirm');
  const [sessionRuntimeMode, setSessionRuntimeMode] = useState<RuntimeMode | null>(null);
  const [sessionNetworkMode, setSessionNetworkMode] = useState<NetworkMode | null>(null);
  const [sessionNetworkModeDefault, setSessionNetworkModeDefault] = useState<NetworkMode | null>(null);
  const [sessionNetworkModeOverridden, setSessionNetworkModeOverridden] = useState(false);
  const [sessionEntry, setSessionEntry] = useState<string>('unknown');
  const [sessionExecutionRevision, setSessionExecutionRevision] = useState<number | null>(null);
  const [draftRuntimeMode, setDraftRuntimeMode] = useState<RuntimeMode>('dev-mode');
  const [draftNetworkMode, setDraftNetworkMode] = useState<NetworkMode>('require-confirm');
  const [sseStatus, setSseStatus] = useState<SSEConnectionStatus>('disconnected');
  const [runningSessionIds, setRunningSessionIds] = useState<Set<string>>(new Set());
  const [relativeTimeClock, setRelativeTimeClock] = useState(0);
  const [creating, setCreating] = useState(false);
  const [installingSocWorkspace, setInstallingSocWorkspace] = useState(false);
  const [suiteInstallProgress, setSuiteInstallProgress] = useState<SuiteInstallProgressState | null>(null);
  const [pendingInitialMessage, setPendingInitialMessage] = useState<string | null>(null);
  const [pendingInitialDisplayText, setPendingInitialDisplayText] = useState<string | null>(null);
  const [pendingOptimisticMessage, setPendingOptimisticMessage] = useState<Message | null>(null);
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [contextPanelWidth, setContextPanelWidth] = useState(() => getInitialSidePanelWidth());
  const [contextSnapshot, setContextSnapshot] = useState<SessionContextSnapshot | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextLoadingMore, setContextLoadingMore] = useState(false);
  const [contextError, setContextError] = useState<string | null>(null);
  const [contextResetVersion, setContextResetVersion] = useState(0);
  const contextScope = useMemo(() => ({
    sessionId: selectedSessionId,
    open: contextPanelOpen,
    resetVersion: contextResetVersion,
    active: false,
    skillSignatures: new Map<string, string>(),
  }), [selectedSessionId, contextPanelOpen, contextResetVersion]);
  const contextScopeRef = useRef(contextScope);
  contextScopeRef.current = contextScope;
  const contextSnapshotRef = useRef<SessionContextSnapshot | null>(null);
  const contextFlightRef = useRef<{
    scope: typeof contextScope;
    controller: AbortController;
    dirty: boolean;
    promise: Promise<void>;
  } | null>(null);
  const [requestedContextResource, setRequestedContextResource] = useState<{ sessionId: string | null; resourceID: string } | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [refreshingProjects, setRefreshingProjects] = useState(true);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [collapsedProjectIds, setCollapsedProjectIds] = useState<Set<string>>(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(collapsedProjectsStorageKey) ?? '[]');
      return new Set(Array.isArray(stored) ? stored.filter((id): id is string => typeof id === 'string') : []);
    } catch {
      return new Set();
    }
  });
  const [collapsedLoadedSessionGroupIds, setCollapsedLoadedSessionGroupIds] = useState<Set<string>>(new Set());
  const [projectsSectionCollapsed, setProjectsSectionCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(projectsSectionCollapsedStorageKey) === 'true';
    } catch {
      return false;
    }
  });
  const [projectDialogMode, setProjectDialogMode] = useState<ProjectDialogMode | null>(null);
  const [openProjectMenuId, setOpenProjectMenuId] = useState<string | null>(null);
  const [projectPendingDelete, setProjectPendingDelete] = useState<ProjectSessionGroup | null>(null);
  const [projectDeleting, setProjectDeleting] = useState(false);
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [projectNameValue, setProjectNameValue] = useState('');
  const [projectWorktreeValue, setProjectWorktreeValue] = useState('');
  const [projectNameManuallyEdited, setProjectNameManuallyEdited] = useState(false);
  const [folderBrowser, setFolderBrowser] = useState<FolderBrowserResponse | null>(null);
  const [folderBrowserLoading, setFolderBrowserLoading] = useState(false);
  const [projectSubmitting, setProjectSubmitting] = useState(false);
  const [batchArchiving, setBatchArchiving] = useState(false);
  const [openMenuSessionId, setOpenMenuSessionId] = useState<string | null>(null);
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [movePickerSessionId, setMovePickerSessionId] = useState<string | null>(null);
  const [movingSessionId, setMovingSessionId] = useState<string | null>(null);
  const [movingTargetProjectId, setMovingTargetProjectId] = useState<string | null>(null);
  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameSubmitting, setRenameSubmitting] = useState(false);
  const [downloadingSessionId, setDownloadingSessionId] = useState<string | null>(null);
  const supportsVision = useDefaultModelVision();
  const [sessionSearchOpen, setSessionSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [agentSourceFilter, setAgentSourceFilter] = useState<AgentSourceFilter>('all');
  const [selectedSessionFallback, setSelectedSessionFallback] = useState<Session | null>(null);
  const [selectorTooltip, setSelectorTooltip] = useState<SelectorTooltip | null>(null);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const renameSubmitInFlightRef = useRef(false);
  const projectSubmitInFlightRef = useRef(false);
  const folderBrowserRequestIdRef = useRef(0);
  const folderBrowserInputPathRef = useRef<string | null>(null);
  const sessionUpdateRefetchTimerRef = useRef<number | null>(null);
  const contextRefetchTimerRef = useRef<number | null>(null);
  const selectedSessionIdRef = useRef(selectedSessionId);
  selectedSessionIdRef.current = selectedSessionId;
  const previousSseStatusRef = useRef<SSEConnectionStatus | null>(null);
  const sessionStatusEventVersionRef = useRef(0);
  const projectListRequestSeqRef = useRef(0);
  const composerResourcesLoadedRef = useRef(false);
  const toast = useToast();

  const sessionProjectIds = useMemo(
    () => [TASK_SESSION_GROUP_ID, ...projects.map((project) => project.id)],
    [projects],
  );
  const sessionListPageSize = projects.length >= 1
    ? MULTI_PROJECT_SESSION_PAGE_SIZE
    : SINGLE_PROJECT_SESSION_PAGE_SIZE;
  const {
    sessions,
    loading: loadingSessions,
    refreshing: refreshingSessions = loadingSessions,
    refetch: refetchSessions,
    updateSessionTitle,
    removeSession,
    removeSessions,
    addSession,
    hasMoreByProject = {},
    loadingMoreProjectIds = new Set<string>(),
    loadMore: loadMoreSessions,
  } = useSessions(searchQuery, {
    projectIds: sessionProjectIds,
    pageSize: sessionListPageSize,
  });
  const searchPanelSessions = useMemo(() => {
    const sortedSessions = [...sessions].sort(
      (left, right) => (right.time?.updated ?? 0) - (left.time?.updated ?? 0),
    );
    return searchQuery.trim()
      ? sortedSessions
      : sortedSessions.slice(0, RECENT_SEARCH_SESSION_LIMIT);
  }, [searchQuery, sessions]);
  const { agents, loading: loadingAgents } = useAgents();
  const { providers, loading: loadingProviders } = useProviders();
  const {
    data: enabledModelDefinitions,
    loading: loadingEnabledModels,
  } = useEnabledChatModelDefinitions();
  const primaryAgents = useMemo(() => agents.filter((a) => a.mode === 'primary' && isAgentUsableInChat(a)), [agents]);
  const subAgents = useMemo(
    () => agents.filter((a) => a.mode !== 'primary' && isAgentUsableInChat(a)),
    [agents],
  );
  const chatAgents = useMemo(() => [...primaryAgents, ...subAgents], [primaryAgents, subAgents]);
  const filteredChatAgents = useMemo(
    () => chatAgents.filter((agent) => {
      if (agent.name.toLowerCase() === 'rex') return false;
      if (agentSourceFilter === 'builtin') return agent.native;
      if (agentSourceFilter === 'custom') return !agent.native;
      return true;
    }),
    [chatAgents, agentSourceFilter],
  );
  const loadComposerResources = useCallback(async () => {
    if (composerResourcesLoadedRef.current || loadingComposerResources) return;
    setLoadingComposerResources(true);
    try {
      const [workflowResult, skillResult] = await Promise.allSettled([
        workflowAPI.listSummaries({ status: 'active' }),
        skillAPI.status(),
      ]);
      if (workflowResult.status === 'fulfilled') {
        setComposerWorkflows(
          workflowResult.value.data
            .filter((workflow) => workflow.status === 'active')
            .sort((a, b) => getWorkflowDisplayName(a, i18n.language)
              .localeCompare(getWorkflowDisplayName(b, i18n.language))),
        );
      }
      if (skillResult.status === 'fulfilled') {
        setComposerSkills(
          skillResult.value.data
            .filter((skill) => (
              !skill.ui_hidden
              && !skill.disabled
              && skill.eligible !== false
              && skill.category !== 'system'
            ))
            .sort((a, b) => a.name.localeCompare(b.name)),
        );
      }
      composerResourcesLoadedRef.current = true;
    } finally {
      setLoadingComposerResources(false);
    }
  }, [i18n.language, loadingComposerResources]);
  const chatModelOptions = useMemo<ChatModelOption[]>(() => {
    const providerById = new Map(
      providers
        .filter((provider) => provider.configured)
        .map((provider) => [provider.id, provider]),
    );

    const formatPricing = (pricing: ModelDefinitionV2['pricing']): string => {
      if (!pricing) return t('modelPicker.noCost');
      if (isPricingFree(pricing)) return t('modelPicker.free');
      return formatPricingPerMillion(pricing);
    };

    const formatContextWindow = (contextWindow?: number): string => {
      if (!contextWindow) return t('modelPicker.contextUnknown');
      const value = contextWindow >= 1000000
        ? `${(contextWindow / 1000000).toFixed(0)}M`
        : `${(contextWindow / 1000).toFixed(0)}K`;
      return t('modelPicker.contextWindow', { value });
    };

    return enabledModelDefinitions.flatMap((model) => {
      if (model.model_type !== 'llm') return [];
      const provider = providerById.get(model.provider_id);
      if (!provider) return [];
      return [{
        key: makeModelKey(provider.id, model.id),
        providerID: provider.id,
        providerName: provider.name || provider.id,
        modelID: model.id,
        label: model.name || model.id,
        pricingLabel: formatPricing(model.pricing),
        contextLabel: formatContextWindow(model.limits?.context_window),
        contextWindowTokens: model.limits?.context_window ?? null,
        supportsVision: typeof model.capabilities?.supports_vision === 'boolean'
          ? model.capabilities.supports_vision
          : null,
      }];
    });
  }, [enabledModelDefinitions, providers, t]);
  const groupedChatModelOptions = useMemo<ChatModelProviderGroup[]>(() => {
    const groups = new Map<string, ChatModelProviderGroup>();

    providers.forEach((provider) => {
      if (!provider.configured) return;
      groups.set(provider.id, {
        providerID: provider.id,
        providerName: provider.name || provider.id,
        models: [],
      });
    });

    chatModelOptions.forEach((option) => {
      const group = groups.get(option.providerID);
      if (group) group.models.push(option);
    });

    return Array.from(groups.values())
      .map((group) => ({
        ...group,
        models: [...group.models].sort((a, b) => a.label.localeCompare(b.label)),
      }))
      .filter((group) => group.models.length > 0)
      .sort((a, b) => a.providerName.localeCompare(b.providerName));
  }, [chatModelOptions, providers]);
  const listedSelectedSession = useMemo(
    () => sessions.find(s => s.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );
  const selectedSession = listedSelectedSession
    ?? (selectedSessionFallback?.id === selectedSessionId ? selectedSessionFallback : null);
  const activeSessionError = sessionLoadError?.sessionId === selectedSessionId ? sessionLoadError : null;
  const activeChatSessionId = selectedSession && !activeSessionError ? selectedSessionId : null;
  const resolvingSelectedSession = legacyNavigationPending || Boolean(selectedSessionId && !selectedSession && !activeSessionError);
  const pinnedModelKey = selectedSession?.model_pinned && selectedSession.provider && selectedSession.model
    ? makeModelKey(selectedSession.provider, selectedSession.model)
    : null;
  const hasPinnedModelOption = !!pinnedModelKey && chatModelOptions.some((option) => option.key === pinnedModelKey);
  const selectedModelAuto = selectedModelKey === AUTO_MODEL_KEY;
  const selectedModelOption = useMemo(
    () => chatModelOptions.find((option) => option.key === selectedModelKey) ?? (selectedModelKey ? null : chatModelOptions[0] ?? null),
    [chatModelOptions, selectedModelKey],
  );
  const {
    data: resolvedDefaultModel,
    initialized: resolvedDefaultModelInitialized,
  } = useResolvedDefaultModel(chatModelOptions.length > 0);
  const primaryModelOption = useMemo(() => {
    if (!resolvedDefaultModel) return null;
    const key = makeModelKey(resolvedDefaultModel.providerID, resolvedDefaultModel.modelID);
    return chatModelOptions.find((option) => option.key === key) ?? null;
  }, [chatModelOptions, resolvedDefaultModel]);
  const autoSelectionAllowed = !selectedSessionId || Boolean(
    selectedSession && ['user', 'entity-config', 'workflow'].includes(selectedSession.category ?? 'user'),
  );
  const canSelectAuto = Boolean(
    autoSelectionAllowed && primaryModelOption,
  );
  const effectiveModelOption = selectedModelAuto ? primaryModelOption : selectedModelOption;
  const selectedPromptModel = selectedModelAuto
    ? null
    : selectedModelOption
      ? { providerID: selectedModelOption.providerID, modelID: selectedModelOption.modelID }
      : null;
  const effectiveSupportsVision = effectiveModelOption?.supportsVision ?? supportsVision;
  const autoStatusLabel = !autoSelectionAllowed
    ? t('modelPicker.autoUserSessionsOnly')
    : primaryModelOption
      ? t('modelPicker.autoHint')
      : t('modelPicker.autoUnavailable');
  const firstChatModelKey = chatModelOptions[0]?.key ?? null;
  const resolvedDefaultKey = resolvedDefaultModel
    ? makeModelKey(resolvedDefaultModel.providerID, resolvedDefaultModel.modelID)
    : null;
  const defaultSelectionKey = resolvedDefaultKey
    && chatModelOptions.some(option => option.key === resolvedDefaultKey)
    ? resolvedDefaultKey
    : firstChatModelKey;

  const toggleProjectCollapsed = useCallback((projectId: string) => {
    setCollapsedProjectIds(prev => {
      const next = new Set(prev);
      next.has(projectId) ? next.delete(projectId) : next.add(projectId);
      return next;
    });
  }, []);

  const setLoadedSessionGroupCollapsed = useCallback((groupId: string, collapsed: boolean) => {
    setCollapsedLoadedSessionGroupIds((current) => {
      const next = new Set(current);
      if (collapsed) next.add(groupId);
      else next.delete(groupId);
      return next;
    });
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        collapsedProjectsStorageKey,
        JSON.stringify(Array.from(collapsedProjectIds)),
      );
    } catch {
      // Project collapse persistence must never block the session manager.
    }
  }, [collapsedProjectIds, collapsedProjectsStorageKey]);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        projectsSectionCollapsedStorageKey,
        String(projectsSectionCollapsed),
      );
    } catch {
      // Section collapse persistence must never block the session manager.
    }
  }, [projectsSectionCollapsed, projectsSectionCollapsedStorageKey]);

  const projectSessionGroups = useMemo<ProjectSessionGroup[]>(() => {
    const registeredIds = new Set(projects.map((project) => project.id));
    const sessionsByProject = new Map<string, Session[]>();
    sessions.forEach((session) => {
      const responseProjectId = session.effectiveProjectID || session.projectID;
      if (!registeredIds.has(responseProjectId)) return;
      const groupSessions = sessionsByProject.get(responseProjectId) ?? [];
      groupSessions.push(session);
      sessionsByProject.set(responseProjectId, groupSessions);
    });

    return projects.map((project) => {
      const projectSessions = sessionsByProject.get(project.id) ?? [];
      return {
        id: project.id,
        label: getProjectLabel(project),
        worktree: project.worktree,
        sessions: projectSessions,
        sessionCount: searchQuery.trim()
          ? project.matchedSessionCount ?? projectSessions.length
          : project.sessionCount ?? projectSessions.length,
        pathStatus: project.pathStatus ?? 'available',
        canWrite: project.canWrite ?? true,
        canDelete: project.canDelete ?? true,
        isShared: project.isShared ?? false,
      };
    })
      .filter((group) => {
        if (!searchQuery.trim()) return true;
        const project = projects.find((item) => item.id === group.id);
        return (project?.matchedSessionCount ?? group.sessions.length) > 0 || group.id === selectedProjectId;
      })
      .sort((a, b) => {
        const aLatest = projects.find((project) => project.id === a.id)?.lastActivityAt ?? a.sessions[0]?.time?.updated ?? 0;
        const bLatest = projects.find((project) => project.id === b.id)?.lastActivityAt ?? b.sessions[0]?.time?.updated ?? 0;
        if (aLatest !== bLatest) return bLatest - aLatest;
        return a.label.localeCompare(b.label);
      });
  }, [projects, searchQuery, selectedProjectId, sessions]);

  const managedProjectSessionGroups = projectSessionGroups;
  const taskSessionGroup = useMemo(
    () => {
      const registeredIds = new Set(projects.map((project) => project.id));
      const taskSessions = sessions.filter((session) => (
        !registeredIds.has(session.effectiveProjectID || session.projectID)
      ));
      return {
        id: TASK_SESSION_GROUP_ID,
        label: t('tasksSection'),
        worktree: '',
        sessions: taskSessions,
        sessionCount: taskSessions.length,
      };
    },
    [projects, sessions, t],
  );
  const taskGroupCollapsed = collapsedProjectIds.has(TASK_SESSION_GROUP_ID);
  const taskGroupSelected = selectedProjectId === TASK_SESSION_GROUP_ID;
  const taskSessionsCollapsedToFirstPage = collapsedLoadedSessionGroupIds.has(TASK_SESSION_GROUP_ID);
  const visibleTaskSessions = taskSessionsCollapsedToFirstPage
    ? taskSessionGroup.sessions.slice(0, sessionListPageSize)
    : taskSessionGroup.sessions;
  const hasMoreRemoteTaskSessions = hasMoreByProject[TASK_SESSION_GROUP_ID] ?? false;
  const canShowMoreTaskSessions = taskSessionsCollapsedToFirstPage || hasMoreRemoteTaskSessions;
  const canCollapseTaskSessions = !taskSessionsCollapsedToFirstPage
    && taskSessionGroup.sessions.length > sessionListPageSize;

  const selectedProjectIDForCreate = selectedProjectId && selectedProjectId !== TASK_SESSION_GROUP_ID
    ? selectedProjectId
    : null;
  const selectedProjectContextLabel = selectedProjectId === TASK_SESSION_GROUP_ID
    ? t('projectPicker.none')
    : projectSessionGroups.find((group) => group.id === selectedProjectId)?.label
      ?? t('projectPicker.none');

  useEffect(() => {
    const selectableProjectIds = new Set([
      TASK_SESSION_GROUP_ID,
      ...projectSessionGroups.map((group) => group.id),
    ]);
    if (selectedProjectId && selectableProjectIds.has(selectedProjectId)) {
      return;
    }

    let storedProjectId: string | null = null;
    try {
      storedProjectId = window.localStorage.getItem(activeProjectStorageKey);
    } catch {
      storedProjectId = null;
    }
    const fallbackProjectId = storedProjectId && selectableProjectIds.has(storedProjectId)
      ? storedProjectId
      : TASK_SESSION_GROUP_ID;
    setSelectedProjectId(fallbackProjectId);
  }, [activeProjectStorageKey, projectSessionGroups, selectedProjectId]);

  useEffect(() => {
    if (
      !selectedProjectId
      || (selectedProjectId !== TASK_SESSION_GROUP_ID
        && !projects.some((project) => project.id === selectedProjectId))
    ) return;
    try {
      window.localStorage.setItem(activeProjectStorageKey, selectedProjectId);
    } catch {
      // Active project persistence must never block the session manager.
    }
  }, [activeProjectStorageKey, projects, selectedProjectId]);

  // Handle SSE events for session-level updates (title changes, etc.)
  const handleChatError = useCallback((msg: string) => {
    toast.error(t('chat.error', 'Error'), msg);
  }, [toast, t]);

  const fetchProjects = useCallback(async (ensureProject?: ProjectSummary, query = '') => {
    const requestSeq = ++projectListRequestSeqRef.current;
    setRefreshingProjects(true);
    try {
      const listResult = await client.get('/api/project', {
        params: { search: query.trim() || undefined },
      });
      if (requestSeq !== projectListRequestSeqRef.current) return;
      const nextProjects = Array.isArray(listResult.data)
        ? listResult.data.filter((project: ProjectSummary) => (
          project.id !== TASK_SESSION_GROUP_ID && !project.isDefault
        ))
        : [];
      setProjects((currentProjects) => {
        if (!ensureProject?.id || nextProjects.some((project) => project.id === ensureProject.id)) {
          return nextProjects;
        }
        const currentProject = currentProjects.find((project) => project.id === ensureProject.id);
        return [{ ...currentProject, ...ensureProject }, ...nextProjects];
      });
    } finally {
      if (requestSeq === projectListRequestSeqRef.current) {
        setRefreshingProjects(false);
      }
    }
  }, []);

  const fetchSessionContext = useCallback((older = false): Promise<void> => {
    const { sessionId, open } = contextScope;
    // A stale callback must not invalidate another session, including A -> B -> A.
    if (contextScopeRef.current !== contextScope || !contextScope.active || !sessionId || !open) return Promise.resolve();
    if (!older && contextRefetchTimerRef.current !== null) {
      window.clearTimeout(contextRefetchTimerRef.current);
      contextRefetchTimerRef.current = null;
    }
    const existing = contextFlightRef.current;
    if (existing?.scope === contextScope) {
      if (!older) existing.dirty = true;
      return existing.promise;
    }
    const snapshot = contextSnapshotRef.current;
    let before = older ? snapshot?.nextBefore : undefined;
    if (older && (!snapshot?.hasMore || !before)) return Promise.resolve();
    const flight = {
      scope: contextScope,
      controller: new AbortController(),
      dirty: false,
      promise: Promise.resolve(),
    };
    const isCurrent = () => contextScopeRef.current === contextScope
      && contextFlightRef.current === flight && !flight.controller.signal.aborted;
    contextFlightRef.current = flight;
    setContextLoading(true);
    setContextLoadingMore(older);
    flight.promise = (async () => {
      try {
        do {
          flight.dirty = false;
          try {
            const page = await sessionApi.getContext(sessionId, before ? { before } : {}, flight.controller.signal);
            if (!isCurrent()) return;
            const merged = mergeSessionContext(contextSnapshotRef.current, page, Boolean(before));
            contextSnapshotRef.current = merged;
            setContextSnapshot(merged);
            setContextError(null);
          } catch (error) {
            if (!isCurrent()) return;
            setContextError(extractErrorMessage(error, 'Failed to load Session Context'));
          }
          // All refreshes received during this flight coalesce into one trailing head read.
          before = undefined;
          if (isCurrent()) setContextLoadingMore(false);
        } while (isCurrent() && flight.dirty);
      } finally {
        if (isCurrent()) {
          contextFlightRef.current = null;
          setContextLoading(false);
          setContextLoadingMore(false);
        }
      }
    })();
    return flight.promise;
  }, [contextScope]);

  const scheduleContextRefetch = useCallback(() => {
    if (contextScopeRef.current !== contextScope || !contextScope.active || !contextScope.open || !contextScope.sessionId) return;
    if (contextFlightRef.current?.scope === contextScope) {
      void fetchSessionContext();
      return;
    }
    if (contextRefetchTimerRef.current !== null) return;
    contextRefetchTimerRef.current = window.setTimeout(() => {
      contextRefetchTimerRef.current = null;
      void fetchSessionContext();
    }, 250);
  }, [contextScope, fetchSessionContext]);

  const scheduleSessionListRefetch = useCallback(() => {
    if (sessionUpdateRefetchTimerRef.current !== null) return;
    sessionUpdateRefetchTimerRef.current = window.setTimeout(() => {
      sessionUpdateRefetchTimerRef.current = null;
      void Promise.all([
        refetchSessions(),
        fetchProjects(undefined, searchQuery),
      ]);
    }, SESSION_UPDATE_REFETCH_DEBOUNCE_MS);
  }, [fetchProjects, refetchSessions, searchQuery]);

  useEffect(() => () => {
    if (sessionUpdateRefetchTimerRef.current !== null) {
      window.clearTimeout(sessionUpdateRefetchTimerRef.current);
      sessionUpdateRefetchTimerRef.current = null;
    }
    if (contextRefetchTimerRef.current !== null) {
      window.clearTimeout(contextRefetchTimerRef.current);
      contextRefetchTimerRef.current = null;
    }
  }, []);

  const handleSSEEvent = useCallback((event: SSEChatEvent) => {
    const eventSessionId = event.properties?.sessionID
      || event.properties?.part?.sessionID
      || event.properties?.info?.sessionID;
    if (
      event.type === 'session.cleared'
      && contextScopeRef.current === contextScope
      && contextScope.active
      && eventSessionId === contextScope.sessionId
    ) {
      // Clearing history invalidates every message-backed resource, even when
      // the panel is closed. Stop old reads before starting a fresh scope.
      contextScope.active = false;
      if (contextRefetchTimerRef.current !== null) {
        window.clearTimeout(contextRefetchTimerRef.current);
        contextRefetchTimerRef.current = null;
      }
      contextFlightRef.current?.controller.abort();
      contextFlightRef.current = null;
      contextScope.skillSignatures.clear();
      contextSnapshotRef.current = null;
      setContextSnapshot(null);
      setContextError(null);
      setRequestedContextResource(null);
      // Remount the panel to cancel previews/folder navigation and clear local
      // errors. The scope effect reloads retained roots if the panel is open.
      setContextResetVersion((version) => version + 1);
      return;
    }
    if (
      contextScopeRef.current === contextScope
      && contextScope.active
      && contextScope.open
      && eventSessionId === contextScope.sessionId
    ) {
      const updatedPart = event.properties?.part;
      let contextPartUpdated = event.type === 'message.part.updated' && (
        updatedPart?.type === 'file'
        || (
          updatedPart?.type === 'tool'
          && updatedPart?.tool === 'write'
          && (updatedPart?.state?.status === 'completed' || updatedPart?.state?.status === 'error')
        )
      );
      if (
        event.type === 'message.part.updated'
        && updatedPart?.type === 'tool'
        && (updatedPart.tool === 'skill_load' || updatedPart.tool === 'load_skill')
        && ['pending', 'running', 'completed', 'error'].includes(updatedPart.state?.status)
      ) {
        const { input, status, error } = updatedPart.state;
        const partID = updatedPart.id || event.properties?.partID;
        const key = JSON.stringify([updatedPart.messageID || event.properties?.messageID, partID]);
        const signature = JSON.stringify([input?.name || input?.skill || null, status, error ?? null]);
        // Only descriptor changes matter, not streamed output. Keep this cache
        // inside the open/session scope so closed, foreign, or stale events cannot seed it.
        if (!partID || contextScope.skillSignatures.get(key) !== signature) {
          if (partID) contextScope.skillSignatures.set(key, signature);
          contextPartUpdated = true;
        }
      }
      if (contextPartUpdated || event.type === 'todo.updated' || event.type === 'session.context.updated') {
        scheduleContextRefetch();
      }
    }
    if (
      event.type === 'session.execution_mode.changed'
      && event.properties?.sessionID === selectedSessionId
      && event.properties?.executionMode === 'build'
    ) {
      setSelectedExecutionMode(DEFAULT_SESSION_EXECUTION_MODE);
      writeSessionExecutionMode(
        selectedSessionId,
        DEFAULT_SESSION_EXECUTION_MODE,
      );
      setShowExecutionModeOptions(false);
      return;
    }
    if (event.type === 'session.notice' && event.properties?.kind === 'directory-fallback') {
      toast.warning(
        t('projectDirectoryFallbackTitle'),
        t('projectDirectoryFallbackDescription', {
          path: event.properties.fallbackDirectory,
        }),
      );
      return;
    }
    if (event.type === 'session.updated' && event.properties?.id) {
      if (event.properties?.title) {
        // Instant local title update so the sidebar reflects the change immediately.
        updateSessionTitle(event.properties.id, event.properties.title);
      }
      // Session/title updates can arrive in bursts when several sessions or
      // background tasks finish together. Coalesce the full sidebar refresh so
      // those bursts don't turn into a request/re-render storm.
      scheduleSessionListRefetch();
    }
  }, [
    contextScope,
    scheduleContextRefetch,
    scheduleSessionListRefetch,
    selectedSessionId,
    t,
    toast,
    updateSessionTitle,
  ]);

  useEffect(() => {
    // Opening the panel must not consume the resource ID set by the message card.
    setRequestedContextResource(null);
    contextSnapshotRef.current = null;
    setContextSnapshot(null);
    setContextError(null);
  }, [selectedSessionId]);

  useEffect(() => {
    contextScope.active = true;
    setContextLoading(false);
    setContextLoadingMore(false);
    if (contextScope.open && contextScope.sessionId) void fetchSessionContext();
    return () => {
      contextScope.active = false;
      contextScope.skillSignatures.clear();
      if (contextRefetchTimerRef.current !== null) {
        window.clearTimeout(contextRefetchTimerRef.current);
        contextRefetchTimerRef.current = null;
      }
      const flight = contextFlightRef.current;
      if (flight?.scope === contextScope) {
        flight.controller.abort();
        contextFlightRef.current = null;
      }
    };
  }, [contextScope, fetchSessionContext]);

  useEffect(() => {
    const previous = previousSseStatusRef.current;
    previousSseStatusRef.current = sseStatus;
    if (
      contextPanelOpen
      && previous
      && previous !== 'connected'
      && sseStatus === 'connected'
      && selectedSessionId
    ) {
      void fetchSessionContext();
    }
  }, [contextPanelOpen, fetchSessionContext, selectedSessionId, sseStatus]);

  useEffect(() => {
    void fetchProjects(undefined, searchQuery);
  }, [fetchProjects, searchQuery]);

  const setSessionRunning = useCallback((sessionId: string, running: boolean) => {
    setRunningSessionIds((current) => {
      if (current.has(sessionId) === running) return current;
      const next = new Set(current);
      if (running) next.add(sessionId);
      else next.delete(sessionId);
      return next;
    });
  }, []);

  const handleSessionStatusEvent = useCallback((event: SSEChatEvent) => {
    if (event.type !== 'session.status') return;
    const sessionId = event.properties?.sessionID;
    if (typeof sessionId !== 'string') return;
    sessionStatusEventVersionRef.current += 1;
    setSessionRunning(sessionId, isRunningSessionStatus(event.properties?.status));
  }, [setSessionRunning]);

  const refreshRunningSessionIds = useCallback(async () => {
    const eventVersion = sessionStatusEventVersionRef.current;
    try {
      const response = await client.get('/api/session/status');
      if (sessionStatusEventVersionRef.current !== eventVersion) return;
      setRunningSessionIds(readRunningSessionIds(response.data));
    } catch {
      // The sidebar status is progressive enhancement; chat remains usable
      // when status recovery is temporarily unavailable.
    }
  }, []);

  useSSE({
    url: `${getApiBase()}/api/event`,
    onEvent: handleSessionStatusEvent,
    onReconnect: refreshRunningSessionIds,
    enabled: true,
    reconnect: { enabled: true, maxRetries: 5, initialDelay: 1000, maxDelay: 10000 },
  });

  useEffect(() => {
    void refreshRunningSessionIds();
  }, [refreshRunningSessionIds]);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setRelativeTimeClock((tick) => tick + 1);
    }, 60_000);
    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    if (!openProjectMenuId) return;
    const closeMenu = () => setOpenProjectMenuId(null);
    window.addEventListener('click', closeMenu);
    return () => window.removeEventListener('click', closeMenu);
  }, [openProjectMenuId]);

  // A pending action belongs only to its original session. Clear it before
  // consuming a new legacy navigation; canonicalization keeps the same ID.
  useEffect(() => {
    if (previousActionSessionRef.current === selectedSessionId) return;
    previousActionSessionRef.current = selectedSessionId;
    setPendingInitialMessage(null);
    setPendingInitialDisplayText(null);
    setPendingInitialSessionId(null);
  }, [selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId || !legacyNavigationPending || legacyNavigationRef.current === location.key) return;
    legacyNavigationRef.current = location.key;
    const legacyId = searchParams.get('session');
    const conflictingTarget = Boolean(routeSessionId && legacyId && routeSessionId !== legacyId);
    const message = searchParams.get('message');
    if (message && !conflictingTarget) {
      setPendingInitialSessionId(selectedSessionId);
      setPendingInitialMessage(message);
      const display = searchParams.get('display');
      setPendingInitialDisplayText(display ? buildInstructionDisplayText(display) : null);
    }
    if (!message || conflictingTarget) {
      setPendingInitialSessionId(null);
      setPendingInitialMessage(null);
      setPendingInitialDisplayText(null);
    }
    if (conflictingTarget) toast.error(t('linkTargetMismatch'));
    const next = new URLSearchParams(searchParams);
    next.delete('session');
    next.delete('message');
    next.delete('display');
    navigate({ pathname: sessionPath(selectedSessionId), search: next.toString(), hash: location.hash }, { replace: true });
  }, [legacyNavigationPending, location.key, location.hash, navigate, routeSessionId, searchParams, selectedSessionId, t, toast]);

  useEffect(() => {
    setPendingFocusMessageId(searchParams.get('focusMessage'));
  }, [selectedSessionId, searchParams]);

  useEffect(() => {
    // A hidden pane waits: the restore runs once the tab is back on screen.
    if (!paneActive || loadingSessions || restoreAttemptRef.current === location.key) return;
    restoreAttemptRef.current = location.key;
    const alreadyVisited = hasVisitedSessionPage();
    markSessionPageVisited();
    if (selectedSessionId || !alreadyVisited || shouldSkipLastSelectedSessionRestore(location.state)) return;
    const lastSelectedSessionId = readLastSelectedSessionId();
    if (lastSelectedSessionId) selectSession(lastSelectedSessionId, true);
  }, [loadingSessions, location.key, location.state, paneActive, selectedSessionId, selectSession]);

  useEffect(() => {
    if (!selectedSessionId || selectedSession?.id !== selectedSessionId) return;
    writeLastSelectedSessionId(selectedSessionId);
  }, [selectedSession?.id, selectedSessionId]);

  useEffect(() => {
    const handoff = executionModeHandoffRef.current;
    if (selectedSessionId && handoff?.sessionId === selectedSessionId) {
      executionModeHandoffRef.current = null;
      setSelectedExecutionMode(handoff.mode);
      return;
    }
    setSelectedExecutionMode(readSessionExecutionMode(selectedSessionId));
  }, [selectedSessionId]);

  useEffect(() => {
    // Do not reuse a list-external detail after leaving its route (it may
    // have been archived or its sharing permissions may have changed).
    setSelectedSessionFallback(current => current?.id === selectedSessionId ? current : null);
  }, [selectedSessionId]);

  useEffect(() => {
    setSessionLoadError(null);
    // Keep a newly created session cached while React commits navigation.
    // Clearing it on the old route can discard the creation handoff.
    if (!selectedSessionId) return;
    if (listedSelectedSession) {
      if (selectedSessionFallback?.id === selectedSessionId) setSelectedSessionFallback(null);
      return;
    }
    if (selectedSessionFallback?.id === selectedSessionId) return;
    // Direct links must not wait for a failed/slow sidebar request.
    let cancelled = false;
    sessionApi.get(selectedSessionId)
      .then((session) => {
        if (cancelled) return;
        if (session.id !== selectedSessionId) throw new Error('Unexpected session response');
        setSelectedSessionFallback(session as unknown as Session);
      })
      .catch((err: any) => {
        if (cancelled) return;
        const status = err?.response?.status ?? err?.status;
        setSelectedSessionFallback(null);
        setSessionLoadError({ sessionId: selectedSessionId, kind: status === 403 ? 'forbidden' : status === 404 ? 'unavailable' : 'failed' });
        setPendingInitialMessage(null);
        setPendingInitialDisplayText(null);
        if (status === 403 || status === 404) writeLastSelectedSessionId(null);
      });
    return () => { cancelled = true; };
  }, [listedSelectedSession, selectedSessionId, selectedSessionFallback?.id, sessionLoadAttempt]);

  // Close agent dropdown on outside click
  useEffect(() => {
    if (!showAgentOptions) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-agent-selector]')) setShowAgentOptions(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [showAgentOptions]);

  useEffect(() => {
    if (!showExecutionModeOptions) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (!target.closest('[data-execution-mode-selector]')) {
        setShowExecutionModeOptions(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setShowExecutionModeOptions(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [showExecutionModeOptions]);

  useEffect(() => {
    if (!showProjectOptions) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-project-selector]')) setShowProjectOptions(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [showProjectOptions]);

  useEffect(() => {
    if (!showModelOptions) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-model-selector]')) setShowModelOptions(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [showModelOptions]);

  useEffect(() => {
    if (!showPermissionModeOptions) return;
    const handle = (event: MouseEvent) => {
      if (!(event.target as HTMLElement).closest('[data-permission-mode-selector]')) {
        setShowPermissionModeOptions(false);
      }
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [showPermissionModeOptions]);

  useEffect(() => {
    void flocksproUsersApi.hasCapability().then(setProPolicyEnabled);
  }, []);

  useEffect(() => {
    if (!proPolicyEnabled || !selectedSessionId) {
      setSessionPermissionMode(null);
      setSessionRuntimeMode(null);
      setSessionNetworkMode(null);
      setSessionNetworkModeDefault(null);
      setSessionNetworkModeOverridden(false);
      setSessionEntry('unknown');
      setSessionExecutionRevision(null);
      return;
    }
    let cancelled = false;
    void flocksproPolicyApi.getSessionExecutionSettings(selectedSessionId)
      .then((result) => {
        if (cancelled) return;
        setSessionPermissionMode(result.permissionMode);
        setSessionRuntimeMode(result.runtimeMode);
        setSessionNetworkMode(result.networkMode);
        setSessionNetworkModeDefault(result.networkModeDefault);
        setSessionNetworkModeOverridden(result.networkModeOverridden);
        setSessionEntry(result.entry);
        setSessionExecutionRevision(result.revision);
      })
      .catch(() => {
        if (cancelled) return;
        setSessionPermissionMode(null);
        setSessionRuntimeMode(null);
        setSessionNetworkMode(null);
        setSessionNetworkModeDefault(null);
        setSessionNetworkModeOverridden(false);
        setSessionEntry('unknown');
        setSessionExecutionRevision(null);
      });
    return () => { cancelled = true; };
  }, [proPolicyEnabled, selectedSessionId]);

  const handlePermissionModeChange = useCallback(async (permissionMode: PermissionMode) => {
    if (!proPolicyEnabled) return;
    if (!selectedSessionId) {
      setDraftPermissionMode(permissionMode);
      return;
    }
    try {
      const updated = await flocksproPolicyApi.setSessionExecutionSettings(selectedSessionId, {
        permissionMode,
        ...(sessionExecutionRevision === null ? {} : { revision: sessionExecutionRevision }),
      });
      setSessionPermissionMode(updated.permissionMode);
      setSessionRuntimeMode(updated.runtimeMode);
      setSessionNetworkMode(updated.networkMode);
      setSessionNetworkModeDefault(updated.networkModeDefault);
      setSessionNetworkModeOverridden(updated.networkModeOverridden);
      setSessionEntry(updated.entry);
      setSessionExecutionRevision(updated.revision);
    } catch (error: unknown) {
      if (isSessionExecutionSettingsUnsupported(error)) return;
      const message = error instanceof Error ? error.message : String(error);
      toast.error(t('chat.error', 'Error'), message);
    }
  }, [proPolicyEnabled, selectedSessionId, sessionExecutionRevision, t, toast]);

  const handleRuntimeModeChange = useCallback(async (runtimeMode: RuntimeMode) => {
    if (!proPolicyEnabled) return;
    if (!selectedSessionId) {
      setDraftRuntimeMode(runtimeMode);
      return;
    }
    try {
      const updated = await flocksproPolicyApi.setSessionExecutionSettings(selectedSessionId, {
        runtimeMode,
        ...(sessionExecutionRevision === null ? {} : { revision: sessionExecutionRevision }),
      });
      setSessionPermissionMode(updated.permissionMode);
      setSessionRuntimeMode(updated.runtimeMode);
      setSessionNetworkMode(updated.networkMode);
      setSessionNetworkModeDefault(updated.networkModeDefault);
      setSessionNetworkModeOverridden(updated.networkModeOverridden);
      setSessionEntry(updated.entry);
      setSessionExecutionRevision(updated.revision);
    } catch (error: unknown) {
      if (isSessionExecutionSettingsUnsupported(error)) return;
      const message = error instanceof Error ? error.message : String(error);
      toast.error(t('chat.error', 'Error'), message);
    }
  }, [proPolicyEnabled, selectedSessionId, sessionExecutionRevision, t, toast]);

  const handleNetworkModeChange = useCallback(async (networkMode: NetworkMode) => {
    if (!proPolicyEnabled) return;
    if (!selectedSessionId) {
      setDraftNetworkMode(networkMode);
      return;
    }
    try {
      const updated = await flocksproPolicyApi.setSessionExecutionSettings(selectedSessionId, {
        networkMode,
        ...(sessionExecutionRevision === null ? {} : { revision: sessionExecutionRevision }),
      });
      setSessionPermissionMode(updated.permissionMode);
      setSessionRuntimeMode(updated.runtimeMode);
      setSessionNetworkMode(updated.networkMode);
      setSessionNetworkModeDefault(updated.networkModeDefault);
      setSessionNetworkModeOverridden(updated.networkModeOverridden);
      setSessionEntry(updated.entry);
      setSessionExecutionRevision(updated.revision);
    } catch (error: unknown) {
      if (isSessionExecutionSettingsUnsupported(error)) return;
      const message = error instanceof Error ? error.message : String(error);
      toast.error(t('chat.error', 'Error'), message);
    }
  }, [proPolicyEnabled, selectedSessionId, sessionExecutionRevision, t, toast]);

  const permissionModeLabels = useMemo<Record<PermissionMode, string>>(
    () => ({
      readonly: t('permissionMode.readonly'),
      'require-confirm': t('permissionMode.requireConfirm'),
      'auto-allow-all': t('permissionMode.autoAllowAll'),
    }),
    [t],
  );

  const permissionModeOptions = useMemo(
    () => ([
      {
        value: 'readonly' as const,
        label: permissionModeLabels.readonly,
        description: t('permissionMode.readonlyDesc'),
      },
      {
        value: 'require-confirm' as const,
        label: permissionModeLabels['require-confirm'],
        description: t('permissionMode.requireConfirmDesc'),
      },
      {
        value: 'auto-allow-all' as const,
        label: permissionModeLabels['auto-allow-all'],
        description: t('permissionMode.autoAllowAllDesc'),
      },
    ]),
    [permissionModeLabels, t],
  );
  const runtimeModeLabels = useMemo<Record<RuntimeMode, string>>(
    () => ({
      'dev-mode': t('permissionMode.runtimeDev', '开发模式'),
      'exe-mode': t('permissionMode.runtimeExe', '执行模式'),
    }),
    [t],
  );
  const runtimeModeOptions = useMemo(
    () => ([
      {
        value: 'dev-mode' as const,
        label: runtimeModeLabels['dev-mode'],
        description: t('permissionMode.runtimeDevDesc', 'Plugins 与代码目录按当前会话权限模式处理'),
      },
      {
        value: 'exe-mode' as const,
        label: runtimeModeLabels['exe-mode'],
        description: t('permissionMode.runtimeExeDesc', 'Plugins 与代码目录固定 readonly'),
      },
    ]),
    [runtimeModeLabels, t],
  );
  const networkModeLabels = useMemo<Record<NetworkMode, string>>(
    () => ({
      'auto-deny-all': t('permissionMode.networkAutoDenyAll', '自动拒绝全部'),
      'require-confirm': t('permissionMode.networkRequireConfirm', '访问前确认'),
      'auto-allow-all': t('permissionMode.networkAutoAllowAll', '自动允许全部'),
    }),
    [t],
  );
  const networkModeOptions = useMemo(
    () => ([
      {
        value: 'auto-deny-all' as const,
        label: networkModeLabels['auto-deny-all'],
        description: t('permissionMode.networkAutoDenyAllDesc', '未命中白名单的网络请求全部拒绝。'),
      },
      {
        value: 'require-confirm' as const,
        label: networkModeLabels['require-confirm'],
        description: t('permissionMode.networkRequireConfirmDesc', '未命中白名单时弹出独立网络确认。'),
      },
      {
        value: 'auto-allow-all' as const,
        label: networkModeLabels['auto-allow-all'],
        description: t('permissionMode.networkAutoAllowAllDesc', '未命中黑白名单时自动放行。'),
      },
    ]),
    [networkModeLabels, t],
  );
  const currentPermissionMode = selectedSessionId
    ? (sessionPermissionMode ?? 'require-confirm')
    : draftPermissionMode;
  const currentRuntimeMode = selectedSessionId
    ? (sessionRuntimeMode ?? 'dev-mode')
    : draftRuntimeMode;
  const currentNetworkMode = selectedSessionId
    ? (sessionNetworkMode ?? 'require-confirm')
    : draftNetworkMode;

  useEffect(() => {
    if (selectedSession?.model_auto) {
      setSelectedModelKey(AUTO_MODEL_KEY);
      return;
    }

    if (!firstChatModelKey) {
      setSelectedModelKey(null);
      return;
    }

    if (hasPinnedModelOption && pinnedModelKey) {
      setSelectedModelKey(pinnedModelKey);
      return;
    }

    setSelectedModelKey(null);
    if (!resolvedDefaultModelInitialized) return;
    setSelectedModelKey(defaultSelectionKey);
  }, [
    defaultSelectionKey,
    firstChatModelKey,
    hasPinnedModelOption,
    pinnedModelKey,
    resolvedDefaultModelInitialized,
    selectedSession?.model_auto,
    selectedSessionId,
  ]);

  useEffect(() => {
    if (loadingEnabledModels || chatModelOptions.length === 0 || !selectedModelKey || selectedModelAuto) return;
    if (chatModelOptions.some((option) => option.key === selectedModelKey)) return;
    setSelectedModelKey(chatModelOptions[0].key);
  }, [chatModelOptions, loadingEnabledModels, selectedModelAuto, selectedModelKey]);

  useEffect(() => {
    if (
      showAgentOptions
      || showExecutionModeOptions
      || showModelOptions
      || showProjectOptions
    ) return;
    setSelectorTooltip(null);
  }, [
    showAgentOptions,
    showExecutionModeOptions,
    showModelOptions,
    showProjectOptions,
  ]);

  useEffect(() => {
    if (!openMenuSessionId) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-session-actions]') && !target.closest('[data-session-menu-portal]')) {
        setOpenMenuSessionId(null);
        setMenuAnchor(null);
        setMovePickerSessionId(null);
      }
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [openMenuSessionId]);

  useEffect(() => {
    if (!renamingSessionId) return;
    renameInputRef.current?.focus();
    renameInputRef.current?.select();
  }, [renamingSessionId]);

  useEffect(() => {
    if (!selectMode) return;
    setOpenMenuSessionId(null);
    setMovePickerSessionId(null);
    setRenamingSessionId(null);
    setRenameValue('');
  }, [selectMode]);

  const handleSelectExecutionMode = useCallback((mode: SessionExecutionMode) => {
    setSelectedExecutionMode(mode);
    setShowExecutionModeOptions(false);
    if (mode !== 'goal') {
      writeSessionExecutionMode(
        selectedSessionId,
        mode as PersistentSessionExecutionMode,
      );
    }
  }, [selectedSessionId]);

  const handleExecutionModeAccepted = useCallback((mode: SessionExecutionMode) => {
    if (mode !== 'goal') return;
    setSelectedExecutionMode(DEFAULT_SESSION_EXECUTION_MODE);
    writeSessionExecutionMode(
      selectedSessionId,
      DEFAULT_SESSION_EXECUTION_MODE,
    );
  }, [selectedSessionId]);

  const handleOpenContextFile = useCallback((resourceId: string) => {
    setRequestedContextResource({ sessionId: selectedSessionIdRef.current, resourceID: resourceId });
    setContextPanelOpen(true);
  }, []);

  const handleResizeContextPanel = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (window.innerWidth < 1024) return;
    const startX = event.clientX;
    const startWidth = contextPanelWidth;
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    const onMove = (moveEvent: PointerEvent) => {
      const next = startWidth + startX - moveEvent.clientX;
      setContextPanelWidth(Math.min(getMaxSidePanelWidth(), Math.max(SIDE_PANEL_MIN_WIDTH, next)));
    };
    const onEnd = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onEnd);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onEnd, { once: true });
  }, [contextPanelWidth]);

  const handleStartNewSession = useCallback(() => {
    writeLastSelectedSessionId(null);
    selectSession(null);
    setSelectedSessionFallback(null);
    setPendingInitialMessage(null);
    setPendingInitialDisplayText(null);
    setContextPanelOpen(false);
    setContextSnapshot(null);
    setRequestedContextResource(null);
    setSelectedAgent('rex');
    // Starting another blank draft keeps the session ID null, so the model
    // initialization effect will not run again. Restore the default explicitly.
    setSelectedModelKey(resolvedDefaultModelInitialized ? defaultSelectionKey : null);
    setSseStatus('disconnected');
    setShowAgentOptions(false);
    setShowModelOptions(false);
    setShowProjectOptions(false);
  }, [defaultSelectionKey, resolvedDefaultModelInitialized, selectSession]);

  const handleCreateSession = useCallback(async (projectIdOverride?: string) => {
    if (creating) return;
    const targetGroupId = projectIdOverride ?? selectedProjectId ?? TASK_SESSION_GROUP_ID;
    const projectID = targetGroupId === TASK_SESSION_GROUP_ID ? null : targetGroupId;
    const carryAutoSelection = !selectedSessionId && selectedModelAuto;
    const navigationKey = navigationRef.current.key;
    setCreating(true);
    try {
      const response = await client.post('/api/session', {
        title: 'New Session',
        ...(projectID ? { projectID } : {}),
        ...(carryAutoSelection ? { model_auto: true } : {}),
      });
      addSession(response.data);
      await fetchProjects(undefined, searchQuery);
      if (!mountedRef.current || navigationRef.current.key !== navigationKey) return;
      setSelectedSessionFallback(response.data);
      setSelectedProjectId(targetGroupId);
      setCollapsedProjectIds(prev => {
        const next = new Set(prev);
        next.delete(targetGroupId);
        return next;
      });
      setSelectedAgent('rex');
      executionModeHandoffRef.current = {
        sessionId: response.data.id,
        mode: DEFAULT_SESSION_EXECUTION_MODE,
      };
      resetDraftExecutionMode();
      writeSessionExecutionMode(
        response.data.id,
        DEFAULT_SESSION_EXECUTION_MODE,
      );
      setSelectedExecutionMode(DEFAULT_SESSION_EXECUTION_MODE);
      setShowExecutionModeOptions(false);
      setSelectedModelKey(carryAutoSelection ? AUTO_MODEL_KEY : null);
      selectSession(response.data.id);
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    } finally {
      setCreating(false);
    }
  }, [creating, selectedProjectId, selectedSessionId, selectedModelAuto, addSession, fetchProjects, searchQuery, toast, t, selectSession]);

  const handleCreateSessionInProject = useCallback((projectId: string) => {
    void handleCreateSession(projectId);
  }, [handleCreateSession]);

  const handleSelectModel = useCallback(async (option: ChatModelOption) => {
    const previousModelKey = selectedModelKey;
    setSelectedModelKey(option.key);
    setShowModelOptions(false);
    if (!selectedSessionId) return;

    try {
      await sessionApi.update(selectedSessionId, {
        provider: option.providerID,
        model: option.modelID,
        model_pinned: true,
        model_auto: false,
      });
      refetchSessions();
    } catch (err: any) {
      setSelectedModelKey(previousModelKey);
      toast.error(t('chat.error', 'Error'), err.message);
    }
  }, [refetchSessions, selectedModelKey, selectedSessionId, toast, t]);

  const handleSelectAutoModel = useCallback(async () => {
    if (!canSelectAuto) return;
    const previousModelKey = selectedModelKey;
    setSelectedModelKey(AUTO_MODEL_KEY);
    setShowModelOptions(false);
    if (!selectedSessionId) return;

    try {
      await sessionApi.update(selectedSessionId, {
        model_auto: true,
        model_pinned: false,
      });
      refetchSessions();
    } catch (err: any) {
      setSelectedModelKey(previousModelKey);
      toast.error(t('chat.error', 'Error'), err.message);
    }
  }, [canSelectAuto, refetchSessions, selectedModelKey, selectedSessionId, toast, t]);

  const handleCreateAndSend = useCallback(async (
    text: string,
    fileParts?: FilePartData[],
    agentOverride?: string,
    modelOverride?: { providerID: string; modelID: string } | null,
    options?: PromptDisplayOptions,
    executionModeOverride?: SessionExecutionMode,
  ) => {
    const navigationKey = navigationRef.current.key;
    try {
      const effectiveExecutionMode = executionModeOverride || selectedExecutionMode;
      const response = await client.post('/api/session', {
        title: 'New Session',
        ...(selectedProjectIDForCreate ? { projectID: selectedProjectIDForCreate } : {}),
        ...(selectedModelAuto ? { model_auto: true } : {}),
      });
      const newSessionId = response.data.id;
      const messageId = createMessageId();
      const effectiveAgent = agentOverride || selectedAgent || 'rex';
      const optimisticParts: Message['parts'] = [];
      if (text || options?.displayText) {
        optimisticParts.push({
          id: `temp-${messageId}-text`,
          type: 'text',
          text,
          ...(options?.displayText ? { metadata: { displayText: options.displayText } } : {}),
        });
      }
      fileParts?.forEach((file, index) => {
        optimisticParts.push({
          id: file.id || `temp-${messageId}-file-${index}`,
          type: 'file',
          url: file.url,
          mime: file.mime,
          filename: file.filename,
        });
      });

      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, fileParts),
        messageID: messageId,
      };
      if (effectiveAgent) payload.agent = effectiveAgent;
      if (!selectedModelAuto && modelOverride) payload.model = modelOverride;
      if (options?.displayText) payload.displayText = options.displayText;
      payload.executionMode = effectiveExecutionMode;

      if (proPolicyEnabled) {
        try {
          const updated = await flocksproPolicyApi.setSessionExecutionSettings(newSessionId, {
            permissionMode: draftPermissionMode,
            runtimeMode: draftRuntimeMode,
            networkMode: draftNetworkMode,
          });
          if (mountedRef.current && navigationRef.current.key === navigationKey) {
            setSessionPermissionMode(updated.permissionMode);
            setSessionRuntimeMode(updated.runtimeMode);
            setSessionNetworkMode(updated.networkMode);
            setSessionNetworkModeDefault(updated.networkModeDefault);
            setSessionNetworkModeOverridden(updated.networkModeOverridden);
            setSessionEntry(updated.entry);
            setSessionExecutionRevision(updated.revision);
          }
        } catch (error: unknown) {
          if (isSessionExecutionSettingsUnsupported(error)) {
            // Backward compatibility: older backends may not expose execution-settings yet.
            // Session creation and prompt sending should continue without interruption.
          } else {
            const message = error instanceof Error ? error.message : String(error);
            toast.error(t('chat.error', 'Error'), message);
            throw error;
          }
        }
      }
      await client.post(`/api/session/${newSessionId}/prompt_async`, payload);

      addSession(response.data);
      void fetchProjects(undefined, searchQuery).catch(() => {});
      if (!mountedRef.current || navigationRef.current.key !== navigationKey) return;
      setSelectedSessionFallback(response.data);
      executionModeHandoffRef.current = {
        sessionId: newSessionId,
        mode: effectiveExecutionMode,
      };
      if (effectiveExecutionMode === 'goal') {
        writeSessionExecutionMode(
          newSessionId,
          DEFAULT_SESSION_EXECUTION_MODE,
        );
        resetDraftExecutionMode();
      } else {
        promoteDraftExecutionMode(
          newSessionId,
          effectiveExecutionMode as PersistentSessionExecutionMode,
        );
      }
      setSelectedModelKey(selectedModelAuto ? AUTO_MODEL_KEY : null);
      setPendingOptimisticMessage({
        id: messageId,
        sessionID: newSessionId,
        role: 'user',
        parts: optimisticParts.length > 0
          ? optimisticParts
          : [{ id: `temp-${messageId}-part`, type: 'text', text }],
        timestamp: Date.now(),
        agent: effectiveAgent,
      });
      selectSession(newSessionId);
      if (effectiveExecutionMode === 'goal') {
        setSelectedExecutionMode(DEFAULT_SESSION_EXECUTION_MODE);
        writeSessionExecutionMode(
          newSessionId,
          DEFAULT_SESSION_EXECUTION_MODE,
        );
      }
    } catch (err: any) {
      toast.error(t('chat.sendFailed', 'Send failed'), err.message);
      throw err;
    }
  }, [
    selectSession,
    addSession,
    fetchProjects,
    searchQuery,
    selectedAgent,
    selectedExecutionMode,
    selectedModelAuto,
    selectedProjectIDForCreate,
    draftPermissionMode,
    draftRuntimeMode,
    draftNetworkMode,
    proPolicyEnabled,
    toast,
    t,
  ]);

  const handleSuiteInstallProgress = useCallback((progress: HubInstallProgressEvent) => {
    setSuiteInstallProgress(current => applySuiteInstallProgressEvent(current, progress));
  }, []);

  const handleAlertOperationsSetup = useCallback(async () => {
    if (installingSocWorkspace) return;

    setInstallingSocWorkspace(true);
    let startedComponentInstall = false;
    try {
      const { data } = await hubAPI.catalog({ type: 'component', q: SOC_WORKSPACE_COMPONENT_ID });
      const component = data.find((item) => item.id === SOC_WORKSPACE_COMPONENT_ID && item.type === 'component');

      if (!component) {
        toast.error(t('welcome.socComponentMissingTitle'), t('welcome.socComponentMissingDescription'));
        return;
      }

      if (!INSTALLED_HUB_STATES.has(component.state)) {
        const confirmed = window.confirm(t('welcome.socComponentInstallConfirm'));
        if (!confirmed) return;
        startedComponentInstall = true;
        setSuiteInstallProgress(createSuiteInstallProgressState(component));
        await hubAPI.installStream('component', SOC_WORKSPACE_COMPONENT_ID, handleSuiteInstallProgress);
      }

      await handleCreateAndSend(
        t('welcome.alertOperationsSuggestion'),
        [],
        undefined,
        selectedPromptModel,
        { displayText: buildInstructionDisplayText(t('welcome.alertOperations')) },
      );
    } catch (err: any) {
      const message = err?.message ?? t('welcome.socComponentInstallFailedDescription');
      if (startedComponentInstall) {
        setSuiteInstallProgress(current => failSuiteInstallProgress(current, {
          id: SOC_WORKSPACE_COMPONENT_ID,
          name: 'SOC Workspace Component',
          nameCn: 'SOC 工作区场景套件',
        }, message));
      }
      toast.error(
        t('welcome.socComponentInstallFailedTitle'),
        message,
      );
    } finally {
      setInstallingSocWorkspace(false);
    }
  }, [handleCreateAndSend, handleSuiteInstallProgress, installingSocWorkspace, selectedPromptModel, t, toast]);

  const showSelectorTooltip = useCallback((target: HTMLElement, title: string, lines: string[]) => {
    const rect = target.getBoundingClientRect();
    setSelectorTooltip({
      title,
      lines,
      x: rect.left - 8,
      y: rect.top + rect.height / 2,
    });
  }, []);

  const handleArchiveSession = useCallback(async (sessionId: string) => {
    const target = sessions.find((s) => s.id === sessionId);
    if (target?.canDelete === false) {
      toast.error(t('archiveFailed'), i18n.t('auth:error.noPermissionToDeleteSession') as string);
      return;
    }
    if (!confirm(t('confirmArchive'))) return;
    try {
      await sessionApi.archive(sessionId);
    } catch (err: any) {
      toast.error(t('archiveFailed'), err.message);
      return;
    }
    // The mutation is complete. A secondary project-count refresh must not
    // turn a successful archive into an error or re-enable the removed row.
    if (navigationRef.current.sessionId === sessionId) selectSession(null, true);
    setSelectedSessionFallback(current => current?.id === sessionId ? null : current);
    removeSession(sessionId);
    toast.success(t('archiveSuccess'));
    try {
      await fetchProjects(undefined, searchQuery);
    } catch {
      // SSE/reconnect refreshes will reconcile project counts later.
    }
  }, [fetchProjects, removeSession, searchQuery, selectedSessionId, toast, t, selectSession]);

  const handleStartRename = useCallback((sessionId: string, currentTitle: string) => {
    setOpenMenuSessionId(null);
    setRenamingSessionId(sessionId);
    setRenameValue(currentTitle);
  }, []);

  const handleCancelRename = useCallback(() => {
    if (renameSubmitting) return;
    renameSubmitInFlightRef.current = false;
    setRenamingSessionId(null);
    setRenameValue('');
  }, [renameSubmitting]);

  const handleSubmitRename = useCallback(async (sessionId: string) => {
    if (renameSubmitInFlightRef.current) return;
    const nextTitle = renameValue.trim();
    if (!nextTitle) {
      toast.error(t('renameFailed'), t('renameEmpty'));
      return;
    }
    const currentSession = sessions.find(session => session.id === sessionId);
    if (currentSession?.title === nextTitle) {
      setRenamingSessionId(null);
      setRenameValue('');
      return;
    }

    renameSubmitInFlightRef.current = true;
    setRenameSubmitting(true);
    try {
      const updatedSession = await sessionApi.update(sessionId, { title: nextTitle });
      updateSessionTitle(sessionId, updatedSession.title ?? nextTitle);
      setRenamingSessionId(null);
      setRenameValue('');
    } catch (err: any) {
      toast.error(t('renameFailed'), err.message);
    } finally {
      renameSubmitInFlightRef.current = false;
      setRenameSubmitting(false);
    }
  }, [renameValue, sessions, t, toast, updateSessionTitle]);

  const handleOpenCreateProject = useCallback(() => {
    setProjectDialogMode('create');
    setEditingProjectId(null);
    setProjectWorktreeValue('');
    setProjectNameValue('');
    setProjectNameManuallyEdited(false);
    setFolderBrowser(null);
  }, []);

  const handleOpenRenameProject = useCallback((project: ProjectSessionGroup) => {
    const persistedName = projects.find((item) => item.id === project.id)?.name?.trim();
    setOpenProjectMenuId(null);
    setProjectDialogMode('rename');
    setEditingProjectId(project.id);
    setProjectNameValue(persistedName && persistedName !== getPathBasename(project.worktree)
      ? persistedName
      : project.label);
    setProjectWorktreeValue(project.worktree);
    setProjectNameManuallyEdited(true);
    setFolderBrowser(null);
  }, [projects]);

  const handleCloseProjectDialog = useCallback(() => {
    if (projectSubmitting) return;
    setProjectDialogMode(null);
    setEditingProjectId(null);
    setProjectNameValue('');
    setProjectWorktreeValue('');
    setProjectNameManuallyEdited(false);
    setFolderBrowser(null);
  }, [projectSubmitting]);

  const loadFolderBrowser = useCallback(async (
    path?: string,
    options?: { preserveInput?: boolean; silent?: boolean },
  ) => {
    const requestId = ++folderBrowserRequestIdRef.current;
    setFolderBrowserLoading(true);
    try {
      const response = await client.get('/api/project/folders', {
        params: { path: path?.trim() || undefined },
      });
      if (requestId !== folderBrowserRequestIdRef.current) return;
      const browser = response.data as FolderBrowserResponse;
      setFolderBrowser(browser);
      if (options?.preserveInput) {
        folderBrowserInputPathRef.current = path?.trim() || browser.path;
      } else {
        folderBrowserInputPathRef.current = browser.path;
        setProjectWorktreeValue(browser.path);
      }
    } catch (err: any) {
      if (requestId === folderBrowserRequestIdRef.current && !options?.silent) {
        toast.error(t('projectDialog.folderBrowseFailed'), err.message);
      }
    } finally {
      if (requestId === folderBrowserRequestIdRef.current) {
        setFolderBrowserLoading(false);
      }
    }
  }, [t, toast]);

  useEffect(() => {
    if (!folderBrowser) return undefined;
    const path = projectWorktreeValue.trim();
    if (
      !path
      || path === folderBrowser.path
      || path === folderBrowserInputPathRef.current
    ) return undefined;

    const timer = window.setTimeout(() => {
      void loadFolderBrowser(path, { preserveInput: true, silent: true });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [folderBrowser, loadFolderBrowser, projectWorktreeValue]);

  const handleSelectProjectFolder = useCallback((path: string) => {
    setProjectWorktreeValue(path);
    if (!projectNameManuallyEdited) {
      setProjectNameValue(getPathBasename(path));
    }
    setFolderBrowser(null);
  }, [projectNameManuallyEdited]);

  const handleCopyProjectPath = useCallback(async (project: ProjectSessionGroup) => {
    setOpenProjectMenuId(null);
    try {
      await navigator.clipboard.writeText(project.worktree);
      toast.success(t('projectDialog.pathCopied'));
    } catch (err: any) {
      toast.error(t('projectDialog.copyPathFailed'), err.message);
    }
  }, [t, toast]);

  const handleShareProject = useCallback(async (
    project: ProjectSessionGroup,
    nextShared: boolean,
  ) => {
    setOpenProjectMenuId(null);
    try {
      const action = nextShared ? 'share-local' : 'unshare-local';
      await client.post(`/api/project/${project.id}/${action}`);
      toast.success(t(nextShared ? 'shareEnabled' : 'shareDisabled'));
      await Promise.all([
        fetchProjects(undefined, searchQuery),
        refetchSessions(),
      ]);
    } catch (err: any) {
      toast.error(t('shareUpdateFailed'), err.message);
    }
  }, [fetchProjects, refetchSessions, searchQuery, t, toast]);

  const handleOpenDeleteProject = useCallback((project: ProjectSessionGroup) => {
    setOpenProjectMenuId(null);
    setProjectPendingDelete(project);
  }, []);

  const handleDeleteProject = useCallback(async () => {
    if (!projectPendingDelete || projectDeleting) return;

    setProjectDeleting(true);
    try {
      await client.delete(`/api/project/${projectPendingDelete.id}`);
      setProjects((current) => current.filter((project) => project.id !== projectPendingDelete.id));
      setCollapsedProjectIds((current) => {
        const next = new Set(current);
        next.delete(projectPendingDelete.id);
        return next;
      });
      setSelectedProjectId((current) => (
        current === projectPendingDelete.id ? TASK_SESSION_GROUP_ID : current
      ));
      setProjectPendingDelete(null);
      await Promise.all([
        fetchProjects(undefined, searchQuery),
        refetchSessions(),
      ]);
      toast.success(t('projectDialog.deleteSuccess'));
    } catch (err: any) {
      toast.error(t('projectDialog.deleteFailed'), err.message);
    } finally {
      setProjectDeleting(false);
    }
  }, [fetchProjects, projectDeleting, projectPendingDelete, refetchSessions, searchQuery, t, toast]);

  const handleSubmitProject = useCallback(async () => {
    if (!projectDialogMode || projectSubmitInFlightRef.current) return;
    const nextWorktree = projectWorktreeValue.trim() || folderBrowser?.path.trim() || '';
    const nextName = projectNameValue.trim() || (
      projectDialogMode === 'create' && !projectNameManuallyEdited && nextWorktree
        ? getPathBasename(nextWorktree)
        : ''
    );
    if (!nextName) {
      toast.error(t('projectDialog.saveFailed'), t('projectDialog.nameEmpty'));
      return;
    }
    if (projectDialogMode === 'create' && !nextWorktree) {
      toast.error(t('projectDialog.saveFailed'), t('projectDialog.folderEmpty'));
      return;
    }

    projectSubmitInFlightRef.current = true;
    setProjectSubmitting(true);
    try {
      if (projectDialogMode === 'create') {
        const response = await client.post('/api/project', {
          name: nextName,
          worktree: nextWorktree,
        });
        const createdProject = response.data as ProjectSummary;
        setProjects(prev => (
          prev.some((project) => project.id === createdProject.id)
            ? prev.map((project) => (project.id === createdProject.id ? createdProject : project))
            : [createdProject, ...prev]
        ));
        setSelectedProjectId(createdProject.id);
        setCollapsedProjectIds(prev => {
          const next = new Set(prev);
          next.delete(createdProject.id);
          return next;
        });
        await fetchProjects(createdProject);
      } else if (editingProjectId) {
        const response = await client.patch(`/api/project/${editingProjectId}`, { name: nextName });
        await fetchProjects(response.data as ProjectSummary);
      }
      setProjectDialogMode(null);
      setEditingProjectId(null);
      setProjectNameValue('');
      setProjectWorktreeValue('');
      setProjectNameManuallyEdited(false);
      setFolderBrowser(null);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      toast.error(
        t('projectDialog.saveFailed'),
        typeof detail === 'string' ? detail : err?.message,
      );
    } finally {
      projectSubmitInFlightRef.current = false;
      setProjectSubmitting(false);
    }
  }, [editingProjectId, fetchProjects, folderBrowser?.path, projectDialogMode, projectNameManuallyEdited, projectNameValue, projectWorktreeValue, t, toast]);

  const handleDownloadSession = useCallback(async (sessionId: string, title: string) => {
    setOpenMenuSessionId(null);
    setDownloadingSessionId(sessionId);
    try {
      const [sessionInfo, messages] = await Promise.all([
        sessionApi.get(sessionId),
        sessionApi.getMessages(sessionId),
      ]);
      const exportPayload = {
        info: sessionInfo,
        messages,
      };
      const blob = new Blob([JSON.stringify(exportPayload, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `session-${sanitizeSessionExportName(title || sessionId)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      toast.error(t('downloadFailed'), err.message);
    } finally {
      setDownloadingSessionId(null);
    }
  }, [t, toast]);

  const handleShareSession = useCallback(async (sessionId: string, nextShared: boolean) => {
    try {
      if (nextShared) {
        await sessionApi.shareLocal(sessionId);
        toast.success(t('shareEnabled'));
      } else {
        await sessionApi.unshareLocal(sessionId);
        toast.success(t('shareDisabled'));
      }
      await refetchSessions();
    } catch (err: any) {
      toast.error(t('shareUpdateFailed'), err.message);
    }
  }, [refetchSessions, t, toast]);

  const handleMoveSessionToProject = useCallback(async (
    sessionId: string,
    project: ProjectSummary,
  ) => {
    if (movingSessionId) return;
    setMovingSessionId(sessionId);
    setMovingTargetProjectId(project.id);
    try {
      await sessionApi.moveToProject(sessionId, project.id);
      setOpenMenuSessionId(null);
      setMenuAnchor(null);
      setMovePickerSessionId(null);
      setProjectsSectionCollapsed(false);
      setCollapsedProjectIds((current) => {
        const next = new Set(current);
        next.delete(project.id);
        return next;
      });
      toast.success(t('moveToProjectSuccess', { project: getProjectLabel(project) }));
      await Promise.allSettled([
        refetchSessions(),
        fetchProjects(undefined, searchQuery),
      ]);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      toast.error(
        t('moveToProjectFailed'),
        typeof detail === 'string' ? detail : err?.message,
      );
    } finally {
      setMovingSessionId(null);
      setMovingTargetProjectId(null);
    }
  }, [fetchProjects, movingSessionId, refetchSessions, searchQuery, t, toast]);

  const handleEnterSelectMode = useCallback(() => {
    setSelectMode(true);
    setCheckedIds(new Set());
  }, []);

  const handleExitSelectMode = useCallback(() => {
    setSelectMode(false);
    setCheckedIds(new Set());
  }, []);

  const handleToggleCheck = useCallback((sessionId: string) => {
    setCheckedIds(prev => {
      const next = new Set(prev);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  }, []);
  const handleSelectSessionRow = useCallback((sessionId: string) => {
    if (selectMode) {
      handleToggleCheck(sessionId);
    } else {
      selectSession(sessionId);
      if (narrowViewport) setSidebarCollapsed(true);
    }
  }, [handleToggleCheck, narrowViewport, selectMode, selectSession]);
  const handleCloseSessionSearch = useCallback(() => {
    setSessionSearchOpen(false);
    setSearchQuery('');
  }, []);
  const handleSelectSearchResult = useCallback((sessionId: string) => {
    handleSelectSessionRow(sessionId);
    handleCloseSessionSearch();
  }, [handleCloseSessionSearch, handleSelectSessionRow]);
  const handleToggleSessionMenu = useCallback((sessionId: string, trigger: HTMLElement) => {
    setMovePickerSessionId(null);
    setOpenMenuSessionId((current) => {
      if (current === sessionId) {
        setMenuAnchor(null);
        return null;
      }
      setMenuAnchor(trigger);
      return sessionId;
    });
  }, []);

  const handleSelectAll = useCallback(() => {
    if (checkedIds.size === sessions.length) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(sessions.map(s => s.id)));
    }
  }, [checkedIds.size, sessions]);

  const handleBatchArchive = useCallback(async () => {
    if (checkedIds.size === 0 || batchArchiving) return;
    if (!confirm(t('confirmBatchArchive', { count: checkedIds.size }))) return;
    setBatchArchiving(true);
    try {
      const ids = Array.from(checkedIds);
      const succeeded: string[] = [];
      const failed: string[] = [];
      await Promise.all(ids.map(async (id) => {
        try {
          await sessionApi.archive(id);
          succeeded.push(id);
        } catch {
          failed.push(id);
        }
      }));
      if (succeeded.length > 0) {
        setSelectedSessionFallback(current => current && succeeded.includes(current.id) ? null : current);
        removeSessions(succeeded);
        if (navigationRef.current.sessionId && succeeded.includes(navigationRef.current.sessionId)) {
          selectSession(null, true);
        }
        try {
          await fetchProjects(undefined, searchQuery);
        } catch {
          // The archive results remain authoritative; counts reconcile later.
        }
      }
      if (failed.length > 0) {
        setCheckedIds(new Set(failed));
        toast.error(t('batchArchiveFailed', { count: failed.length }));
      } else {
        setCheckedIds(new Set());
        setSelectMode(false);
      }
    } finally {
      setBatchArchiving(false);
    }
  }, [batchArchiving, checkedIds, fetchProjects, removeSessions, searchQuery, selectedSessionId, toast, t, selectSession]);

  const renderSessionListItem = (session: Session) => (
    <div
      key={session.id}
      onClick={() => handleSelectSessionRow(session.id)}
      className={`group relative mx-2 mb-1 px-3 py-2.5 rounded-card border cursor-pointer transition-all duration-150 ${
        !selectMode && selectedSessionId === session.id
          ? 'bg-subtle border-line-control shadow-sm '
          : selectMode && checkedIds.has(session.id)
          ? 'bg-info-soft border-info '
          : 'border-line hover:border-line hover:bg-hover hover:shadow-sm '
      }`}
    >
      <div className="flex items-center gap-1.5 min-w-0 pr-7">
        {selectMode && (
          <input
            type="checkbox"
            checked={checkedIds.has(session.id)}
            onChange={() => handleToggleCheck(session.id)}
            onClick={(e) => e.stopPropagation()}
            className="flex-shrink-0 w-3.5 h-3.5 accent-info cursor-pointer rounded-tag"
          />
        )}
        {session.category === 'workflow' && (
          <span title={t('workflowSession')} className="flex-shrink-0">
            <WorkflowIcon aria-hidden="true" className="w-4 h-4 text-ai" />
          </span>
        )}
        {session.category === 'entity-config' && (
          <span title={t('configSession')} className="flex-shrink-0">
            <IconAdjustmentsHorizontal aria-hidden="true" className="w-4 h-4 text-ai" />
          </span>
        )}
        {renamingSessionId === session.id ? (
          <input
            ref={renameInputRef}
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={() => void handleSubmitRename(session.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); void handleSubmitRename(session.id); }
              if (e.key === 'Escape') { e.preventDefault(); handleCancelRename(); }
            }}
            placeholder={t('renamePlaceholder')}
            disabled={renameSubmitting}
            className="ui-input w-full min-w-0 rounded-tag border border-info bg-surface px-1.5  text-sm text-fg focus:border-action"
            aria-label={t('rename')}
            data-session-rename-input
          />
        ) : (
          <h3 className="font-semibold text-fg truncate text-sm flex items-center gap-1.5 ">
            <span className="truncate">{session.title}</span>
            {session.isShared && (
              <span className="inline-flex items-center rounded-pill border border-info bg-info-soft px-1.5 py-0.5 text-xs font-medium text-info">
                {t('sharedTag')}
              </span>
            )}
          </h3>
        )}
      </div>
      {session.time?.updated && renamingSessionId !== session.id && (
        <p className="mt-1 text-xs text-fg-muted truncate pl-0.5 ">
          {formatSessionDate(session.time.updated)}
        </p>
      )}

      {!selectMode && (
        <div className="absolute right-1.5 top-2" data-session-actions>
          <button
            onClick={(e) => {
              e.stopPropagation();
              handleToggleSessionMenu(session.id, e.currentTarget);
            }}
            title={t('moreActions')}
            aria-label={t('moreActions')}
            aria-expanded={openMenuSessionId === session.id}
            className={`ui-btn-icon inline-flex items-center justify-center p-1 rounded-control text-fg-muted hover:text-fg-secondary hover:bg-hover transition-all ${
              openMenuSessionId === session.id ? 'opacity-100 text-fg-secondary bg-selected ' : 'opacity-0 group-hover:opacity-100'
            }`}
          >
            <IconDots aria-hidden="true" className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );

  const workbenchRefreshing = refreshingSessions || refreshingProjects || resolvingSelectedSession;
  const workbenchRefreshLabel = resolvingSelectedSession
    ? t('restoringTask')
    : t('refreshingWorkbench');
  const showSessionListSkeleton = loadingSessions && sessions.length === 0;

  return (
    <div className="relative flex h-full w-full overflow-hidden bg-canvas text-fg ">
      {/* ── Sidebar ── */}
      <div
        className={`flex h-full flex-shrink-0 flex-col overflow-hidden border-r bg-subtle transition-[width,opacity] duration-200 ${
          narrowViewport ? 'absolute inset-y-0 left-0 z-30 shadow-overlay' : 'relative'
        } ${
          sidebarCollapsed
            ? 'w-0 border-transparent opacity-0'
            : 'w-sidebar border-line opacity-100 '
        }`}
        aria-label={t('managementTitle')}
        data-session-sidebar
      >
        {/* Header (left padding below lg clears the layout's floating menu button) */}
        <div className="flex-shrink-0 px-3 pb-2 pt-3.5 pl-14 lg:pl-3">
          <div className="mb-2 flex h-8 items-center justify-between px-1">
            <div className="min-w-0">
              <strong className="text-sm font-semibold text-fg ">
                {t('managementTitle')}
              </strong>
              <span className="ml-2 text-xs text-fg-muted ">
                {t('sessionCount', { count: sessions.length })}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setSessionSearchOpen(true)}
              className="ui-btn-icon inline-flex items-center justify-center grid   shrink-0 place-items-center rounded-control text-fg-muted transition-colors hover:bg-hover hover:text-fg "
              title={t('openTaskSearch')}
              aria-label={t('openTaskSearch')}
              aria-expanded={sessionSearchOpen}
            >
              <IconSearch aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>

          <div>
            <div className="relative h-[34px] rounded-control transition-colors hover:bg-hover ">
              {creating
                ? <IconLoader2 aria-hidden="true" className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-fg-muted" />
                : <IconEdit aria-hidden="true" className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted " />}
              <button
                onClick={handleStartNewSession}
                disabled={creating}
                className="h-full w-full rounded-control border-0 bg-transparent pl-9 pr-3 text-left text-sm font-medium text-fg-secondary transition-colors hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
              >
                {t('newSession')}
              </button>
            </div>
          </div>
        </div>

        {sessionSearchOpen && (
          <PageDialog onClose={handleCloseSessionSearch}

            aria-label={t('taskSearchDialog')}


            className="fixed inset-0 z-[80] flex items-start justify-center bg-overlay-backdrop px-4 pt-[18vh] backdrop-blur-[1px] "
          >
            <div
              onClick={(event) => event.stopPropagation()}
              className="w-full max-w-modal overflow-hidden rounded-card border border-line bg-surface shadow-overlay "
            >
              <div className="relative h-[52px] border-b border-line ">
                <IconSearch aria-hidden="true" className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-fg-muted " />
                <input
                  autoFocus
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Escape') {
                      event.preventDefault();
                      handleCloseSessionSearch();
                    }
                  }}
                  placeholder={t('filterConversations', 'Search tasks')}
                  className="ui-input h-full w-full border-0 bg-transparent pl-12 pr-12 text-sm font-medium text-fg-secondary placeholder:text-fg-muted"
                />
                <button
                  type="button"
                  onClick={handleCloseSessionSearch}
                  className="ui-btn-icon inline-flex items-center justify-center absolute right-3 top-1/2 grid   -translate-y-1/2 place-items-center rounded-control text-fg-muted transition-colors hover:bg-hover hover:text-fg "
                  title={t('closeTaskSearch')}
                  aria-label={t('closeTaskSearch')}
                >
                  <IconX aria-hidden="true" className="h-4 w-4" />
                </button>
              </div>
              <div className="max-h-[420px] overflow-y-auto p-2">
                <div className="px-2 pb-1.5 pt-1 text-xs font-medium text-fg-placeholder ">
                  {t(searchQuery.trim() ? 'searchResults' : 'recentTasks')}
                </div>
                {searchPanelSessions.length > 0 ? (
                  <div className="space-y-0.5">
                    {searchPanelSessions.map((session) => (
                      <button
                        key={session.id}
                        type="button"
                        onClick={() => handleSelectSearchResult(session.id)}
                        className="flex h-10 w-full min-w-0 items-center gap-2.5 rounded-card px-3 text-left text-sm text-fg-secondary transition-colors hover:bg-hover hover:text-fg focus:bg-hover "
                      >
                        <span className="h-1.5 w-1.5 shrink-0 rounded-pill bg-info" />
                        <span className="min-w-0 flex-1 truncate font-medium">{session.title}</span>
                        <span className="max-w-[110px] shrink-0 truncate text-xs text-fg-placeholder ">
                          {session.projectName || getPathBasename(session.directory)}
                        </span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="px-3 py-10 text-center text-sm text-fg-placeholder ">
                    {t('noResults')}
                  </div>
                )}
              </div>
            </div>
          </PageDialog>
        )}

        {/* Session list */}
        <div
          data-testid="session-list-scroll"
          className="session-sidebar-scrollbar min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-2 pb-[18px] pt-1.5"
        >
          {showSessionListSkeleton ? (
            <SessionListSkeleton />
          ) : (
          <div>
            <section className="group/projects-section">
                <div className="flex h-7 select-none items-center gap-1.5 px-2 text-xs font-semibold uppercase tracking-[0.02em] text-fg-muted ">
                  <button
                    type="button"
                    onClick={() => setProjectsSectionCollapsed((collapsed) => !collapsed)}
                    className="flex h-6 items-center gap-1 rounded-control px-1 text-left transition-colors hover:bg-hover hover:text-fg-secondary "
                    aria-label={t('toggleProjects')}
                  >
                    <span>{t('projectsSection')}</span>
                    <span className="font-normal tabular-nums text-fg-muted ">
                      ({managedProjectSessionGroups.length})
                    </span>
                    {projectsSectionCollapsed
                      ? <IconChevronRight aria-hidden="true" className="h-4 w-4 shrink-0" />
                      : <IconChevronDown aria-hidden="true" className="h-4 w-4 shrink-0" />}
                  </button>
                  <button
                    type="button"
                    onClick={handleOpenCreateProject}
                    className="ui-btn-icon inline-flex items-center justify-center ml-auto grid   place-items-center rounded-control text-fg-muted transition-colors hover:bg-hover hover:text-fg-secondary "
                    title={t('projectDialog.createTitle')}
                    aria-label={t('projectDialog.createTitle')}
                  >
                    <IconFolderPlus aria-hidden="true" className="h-4 w-4" />
                  </button>
                </div>
                {!projectsSectionCollapsed && (
                  <div>
                {managedProjectSessionGroups.map((group) => {
                  const collapsed = collapsedProjectIds.has(group.id);
                  const isSelectedProject = selectedProjectId === group.id;
                  const sessionsCollapsedToFirstPage = collapsedLoadedSessionGroupIds.has(group.id);
                  const visibleProjectSessions = sessionsCollapsedToFirstPage
                    ? group.sessions.slice(0, sessionListPageSize)
                    : group.sessions;
                  const hasMoreRemoteProjectSessions = (
                    group.sessions.length < group.sessionCount
                    || hasMoreByProject[group.id]
                  );
                  const canShowMoreProjectSessions = sessionsCollapsedToFirstPage
                    || hasMoreRemoteProjectSessions;
                  const canCollapseProjectSessions = !sessionsCollapsedToFirstPage
                    && group.sessions.length > sessionListPageSize;
                  const persistedProject = projects.find((project) => project.id === group.id);
                  return (
                    <div key={group.id} className="group/project relative">
                      <div
                        className={`flex h-[34px] items-center gap-1 rounded-control px-3 text-sm transition-colors ${
                          isSelectedProject
                            ? 'font-semibold text-fg '
                            : 'font-medium text-fg-secondary hover:bg-hover hover:text-fg '
                        }`}
                        title={group.worktree}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedProjectId(group.id);
                            toggleProjectCollapsed(group.id);
                          }}
                          className="flex h-full min-w-0 flex-1 items-center gap-2 rounded-control text-left"
                          aria-label={t('selectProject', { project: group.label })}
                          aria-expanded={!collapsed}
                        >
                          <IconFolderCode aria-hidden="true" className="h-4 w-4 shrink-0 text-fg-muted " />
                          <span className="min-w-0 flex-1 truncate">{group.label}</span>
                          {group.isShared && (
                            <span className="inline-flex shrink-0 items-center rounded-pill border border-info bg-info-soft px-1.5 py-0.5 text-xs font-medium text-info ">
                              {t('sharedTag')}
                            </span>
                          )}
                          {group.pathStatus !== 'available' && (
                            <IconAlertTriangle
                              className="h-4 w-4 shrink-0 text-warning"
                              aria-label={t('projectPathUnavailable')}
                            />
                          )}
                        </button>
                        {persistedProject && (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setOpenProjectMenuId((current) => current === group.id ? null : group.id);
                            }}
                            className={`ui-btn-icon inline-flex items-center justify-center grid   place-items-center rounded-control text-fg-muted transition-all hover:bg-hover hover:text-fg ${
                              openProjectMenuId === group.id || isSelectedProject
                                ? 'opacity-100'
                                : 'opacity-0 group-hover/project:opacity-100 group-focus-within/project:opacity-100'
                            }`}
                            title={t('projectActions')}
                            aria-label={t('projectActions')}
                            aria-expanded={openProjectMenuId === group.id}
                          >
                            <IconDots aria-hidden="true" className="h-4 w-4" />
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            handleCreateSessionInProject(group.id);
                          }}
                          disabled={creating || !group.canWrite || group.pathStatus !== 'available'}
                          className={`ui-btn-icon inline-flex items-center justify-center grid   place-items-center rounded-control text-fg-muted transition-all hover:bg-hover hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled ${
                            isSelectedProject ? 'opacity-100' : 'opacity-0 group-hover/project:opacity-100 group-focus-within/project:opacity-100'
                          }`}
                          title={t('createSessionInProject', { project: group.label })}
                          aria-label={t('createSessionInProject', { project: group.label })}
                        >
                          {creating && isSelectedProject
                            ? <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                            : <IconPlus aria-hidden="true" className="h-4 w-4" />}
                        </button>
                      </div>
                      {persistedProject && openProjectMenuId === group.id && (
                        <div
                          className="absolute right-7 top-8 z-30 grid w-[140px] gap-0.5 rounded-card border border-line bg-surface p-1 shadow-overlay "
                          role="menu"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => void handleCopyProjectPath(group)}
                            className="flex h-[30px] w-full items-center gap-2 rounded-control px-2 text-left text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
                          >
                            <IconCopy aria-hidden="true" className="h-4 w-4" />
                            {t('projectDialog.copyPathAction')}
                          </button>
                          {group.canWrite && (
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => void handleShareProject(group, !group.isShared)}
                              className="flex h-[30px] w-full items-center gap-2 rounded-control px-2 text-left text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
                            >
                              <IconShare aria-hidden="true" className="h-4 w-4" />
                              {t(group.isShared ? 'unshareAction' : 'shareAction')}
                            </button>
                          )}
                          {group.canWrite && (
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => handleOpenRenameProject(group)}
                              className="flex h-[30px] w-full items-center gap-2 rounded-control px-2 text-left text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
                            >
                              <IconEdit aria-hidden="true" className="h-4 w-4" />
                              {t('projectDialog.renameAction')}
                            </button>
                          )}
                          {group.canDelete && (
                            <div className="mx-2 my-0.5 border-t border-line " />
                          )}
                          {group.canDelete && (
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => handleOpenDeleteProject(group)}
                              className="flex h-[30px] w-full items-center gap-2 rounded-control px-2 text-left text-xs text-danger transition-colors hover:bg-danger-soft hover:text-danger disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
                            >
                              <IconTrash aria-hidden="true" className="h-4 w-4" />
                              {t('projectDialog.deleteAction')}
                            </button>
                          )}
                        </div>
                      )}
                      {!collapsed && (
                        <div className="mb-1 mt-0.5">
                          {group.sessions.length > 0 ? (
                            visibleProjectSessions.map((session) => (
                              <SessionSidebarItem
                                key={session.id}
                                session={session}
                                nested
                                selected={selectedSessionId === session.id}
                                selectMode={selectMode}
                                checked={checkedIds.has(session.id)}
                                menuOpen={openMenuSessionId === session.id}
                                running={runningSessionIds.has(session.id)}
                                renaming={renamingSessionId === session.id}
                                renameValue={renameValue}
                                renameSubmitting={renameSubmitting}
                                language={i18n.language}
                                relativeTimeClock={relativeTimeClock}
                                t={t}
                                renameInputRef={renameInputRef}
                                onSelect={handleSelectSessionRow}
                                onToggleCheck={handleToggleCheck}
                                onRenameValueChange={setRenameValue}
                                onSubmitRename={handleSubmitRename}
                                onCancelRename={handleCancelRename}
                                onToggleMenu={handleToggleSessionMenu}
                              />
                            ))
                          ) : (
                            <div className="ml-[19px] px-2.5 py-2 text-xs text-fg-placeholder ">
                              {t('noProjectSessions')}
                            </div>
                          )}
                          {(canShowMoreProjectSessions || canCollapseProjectSessions) && (
                            <div className="ml-[19px] flex h-7 items-center justify-center gap-2 py-0.5">
                              {canShowMoreProjectSessions && (
                              <button
                                type="button"
                                onClick={() => {
                                  if (sessionsCollapsedToFirstPage) {
                                    setLoadedSessionGroupCollapsed(group.id, false);
                                    return;
                                  }
                                  void loadMoreSessions(group.id);
                                }}
                                disabled={loadingMoreProjectIds.has(group.id)}
                                className="flex h-7 items-center justify-center gap-1 rounded-control px-2 text-xs text-fg-placeholder transition-colors hover:bg-hover hover:text-fg-secondary disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
                              >
                                {loadingMoreProjectIds.has(group.id) && (
                                  <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                                )}
                                {t('loadMore')}
                              </button>
                              )}
                              {canCollapseProjectSessions && (
                                <button
                                  type="button"
                                  onClick={() => setLoadedSessionGroupCollapsed(group.id, true)}
                                  className="flex h-7 items-center justify-center rounded-control px-2 text-xs text-fg-placeholder transition-colors hover:bg-hover hover:text-fg-secondary "
                                >
                                  {t('collapseLoaded')}
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
                  </div>
                )}
              </section>
              {taskSessionGroup && (
                <section className="group/tasks-section mt-3">
                  <div className="flex h-7 select-none items-center gap-1 px-2 text-xs font-semibold uppercase tracking-[0.02em] text-fg-muted ">
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedProjectId(taskSessionGroup.id);
                        toggleProjectCollapsed(taskSessionGroup.id);
                      }}
                      className="flex h-6 items-center gap-1 rounded-control px-1 text-left transition-colors hover:bg-hover hover:text-fg-secondary "
                      aria-label={t('toggleTasks')}
                    >
                      <span>{t('tasksSection')}</span>
                      <span className="font-normal tabular-nums text-fg-muted ">
                        ({taskSessionGroup.sessionCount})
                      </span>
                      {taskGroupCollapsed
                        ? <IconChevronRight aria-hidden="true" className="h-4 w-4 shrink-0" />
                        : <IconChevronDown aria-hidden="true" className="h-4 w-4 shrink-0" />}
                    </button>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        void handleCreateSession(TASK_SESSION_GROUP_ID);
                      }}
                      disabled={creating}
                      className="ui-btn-icon inline-flex items-center justify-center ml-auto grid   place-items-center rounded-control text-fg-muted opacity-0 transition-all hover:bg-hover hover:text-fg-secondary group-hover/tasks-section:opacity-100 group-focus-within/tasks-section:opacity-100 disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
                      title={t('createTaskSession')}
                      aria-label={t('createTaskSession')}
                    >
                      {creating && taskGroupSelected
                        ? <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                        : <IconPlus aria-hidden="true" className="h-4 w-4" />}
                    </button>
                  </div>
                  {!taskGroupCollapsed && (
                    <div className="mt-0.5">
                      {visibleTaskSessions.map((session) => (
                        <SessionSidebarItem
                          key={session.id}
                          session={session}
                          selected={selectedSessionId === session.id}
                          selectMode={selectMode}
                          checked={checkedIds.has(session.id)}
                          menuOpen={openMenuSessionId === session.id}
                          running={runningSessionIds.has(session.id)}
                          renaming={renamingSessionId === session.id}
                          renameValue={renameValue}
                          renameSubmitting={renameSubmitting}
                          language={i18n.language}
                          relativeTimeClock={relativeTimeClock}
                          t={t}
                          renameInputRef={renameInputRef}
                          onSelect={handleSelectSessionRow}
                          onToggleCheck={handleToggleCheck}
                          onRenameValueChange={setRenameValue}
                          onSubmitRename={handleSubmitRename}
                          onCancelRename={handleCancelRename}
                          onToggleMenu={handleToggleSessionMenu}
                        />
                      ))}
                      {(canShowMoreTaskSessions || canCollapseTaskSessions) && (
                        <div className="flex h-7 items-center justify-center gap-2 py-0.5">
                          {canShowMoreTaskSessions && (
                          <button
                            type="button"
                            onClick={() => {
                              if (taskSessionsCollapsedToFirstPage) {
                                setLoadedSessionGroupCollapsed(taskSessionGroup.id, false);
                                return;
                              }
                              void loadMoreSessions(taskSessionGroup.id);
                            }}
                            disabled={loadingMoreProjectIds.has(taskSessionGroup.id)}
                            className="flex h-7 items-center justify-center gap-1 rounded-control px-2 text-xs text-fg-placeholder transition-colors hover:bg-hover hover:text-fg-secondary disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
                          >
                            {loadingMoreProjectIds.has(taskSessionGroup.id) && (
                              <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                            )}
                            {t('loadMore')}
                          </button>
                          )}
                          {canCollapseTaskSessions && (
                            <button
                              type="button"
                              onClick={() => setLoadedSessionGroupCollapsed(taskSessionGroup.id, true)}
                              className="flex h-7 items-center justify-center rounded-control px-2 text-xs text-fg-placeholder transition-colors hover:bg-hover hover:text-fg-secondary "
                            >
                              {t('collapseLoaded')}
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </section>
              )}
          </div>
          )}
        </div>

        {/* Bottom：批量操作栏 / 批量选择入口 */}
        {sessions.length > 0 && (
          <div className="flex min-h-11 flex-shrink-0 items-center justify-center border-t border-line px-3 ">
            {selectMode ? (
              <div className="grid grid-cols-3 gap-1.5">
                <button
                  onClick={handleSelectAll}
                  className="ui-btn-icon inline-flex items-center justify-center grid   place-items-center rounded-control text-info transition-colors hover:bg-info-soft "
                  title={checkedIds.size === sessions.length && sessions.length > 0 ? t('deselectAll') : t('selectAll')}
                  aria-label={checkedIds.size === sessions.length && sessions.length > 0 ? t('deselectAll') : t('selectAll')}
                >
                  <IconSquareCheck aria-hidden="true" className="h-4 w-4" />
                </button>
                <button
                  onClick={handleExitSelectMode}
                  className="ui-btn-icon inline-flex items-center justify-center grid   place-items-center rounded-control text-fg-secondary transition-colors hover:bg-hover "
                  title={t('cancelSelect')}
                  aria-label={t('cancelSelect')}
                >
                  <IconX aria-hidden="true" className="h-4 w-4" />
                </button>
                <button
                  onClick={handleBatchArchive}
                  disabled={checkedIds.size === 0 || batchArchiving}
                  className="ui-btn-icon inline-flex items-center justify-center grid   place-items-center rounded-control text-danger transition-colors hover:bg-danger-soft disabled:cursor-default disabled:bg-disabled disabled:text-fg-disabled "
                  title={t('archiveSelected', { count: checkedIds.size })}
                  aria-label={t('archiveSelected', { count: checkedIds.size })}
                >
                  {batchArchiving ? <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <IconArchive aria-hidden="true" className="h-4 w-4" />}
                </button>
              </div>
            ) : (
              <button
                onClick={handleEnterSelectMode}
                className="ui-btn-icon inline-flex items-center justify-center grid   place-items-center rounded-control text-fg-muted transition-colors hover:bg-hover hover:text-fg "
                title={t('selectMode')}
                aria-label={t('selectMode')}
              >
                <IconSquareCheck aria-hidden="true" className="h-4 w-4" />
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── Main area ── */}
      <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-canvas ">
        {/* Header */}
        <header className="relative flex h-[52px] flex-shrink-0 items-center gap-2 px-4 pl-14 text-xs lg:pl-4">
          <div className="shrink-0">
            <button
              onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
              className="ui-btn-icon inline-flex items-center justify-center grid   place-items-center rounded-control border-0 bg-transparent text-fg-muted transition-colors hover:bg-hover hover:text-fg "
              title={sidebarCollapsed ? t('showHistory') : t('hideHistory')}
              aria-label={sidebarCollapsed ? t('showHistory') : t('hideHistory')}
            >
              {sidebarCollapsed ? <IconLayoutSidebar aria-hidden="true" className="h-4 w-4" /> : <IconLayoutSidebarLeftCollapse aria-hidden="true" className="h-4 w-4" />}
            </button>
          </div>

          <div className="flex min-w-0 items-center">
            <h2 className="truncate text-sm font-semibold text-fg-secondary ">
              {selectedSession?.title || t('newSession')}
            </h2>
          </div>

          {selectedSessionId && selectedSession && !activeSessionError && (
            <CopyButton text={new URL(sessionPath(selectedSessionId), window.location.origin).href} label={t('copyLink')} />
          )}

          {workbenchRefreshing && (
            <WorkbenchRefreshStatus label={workbenchRefreshLabel} />
          )}
          {activeChatSessionId && <SSEStatusIndicator status={sseStatus} t={t} />}

          <div className="ml-auto flex items-center">
            <button
              type="button"
              disabled={!activeChatSessionId}
              onClick={() => setContextPanelOpen((open) => !open)}
              className={`flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-control px-2.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled ${
                contextPanelOpen
                  ? 'bg-ai-soft text-ai'
                  : 'text-fg-secondary hover:bg-hover hover:text-fg'
              }`}
              aria-label={t('context.title')}
              title={t('context.title')}
            >
              <IconSparkles aria-hidden="true" className="h-4 w-4" />
              <span>{t('context.title')}</span>
              {(contextSnapshot?.counts.total || 0) > 0 && (
                <span className="rounded-pill bg-current/10 px-1.5 py-0.5 text-xs">
                  {contextSnapshot!.counts.total}
                </span>
              )}
            </button>
          </div>
        </header>

        <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* Chat — powered by unified SessionChat */}
        {activeSessionError ? (
          <div role="alert" className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
            <p>{t(`sessionAccess.${activeSessionError.kind}`)}</p>
            <div className="flex gap-4">
              <button onClick={() => { setSessionLoadError(null); setSessionLoadAttempt(value => value + 1); }}>{t('sessionAccess.retry')}</button>
              <button onClick={() => selectSession(null)}>{t('sessionAccess.back')}</button>
            </div>
          </div>
        ) : resolvingSelectedSession ? (
          <SessionChatSkeleton />
        ) : (
          <SessionChat
            key={activeChatSessionId ?? 'empty-session'}
            sessionId={activeChatSessionId}
            live={Boolean(activeChatSessionId)}
          hideInput={selectedSession?.canWrite === false}
          display={{
            compact: false,
            pageCanvas: true,
            showActions: true,
            showTimestamp: true,
            collapseIntermediateSteps: true,
            processGroupsDefaultOpen: false,
            processGroupsOpenWhileActive: true,
          }}
          agentName={selectedAgent}
          executionMode={selectedExecutionMode}
          onExecutionModeAccepted={handleExecutionModeAccepted}
          mentionAgents={chatAgents}
          className="flex-1 min-h-0"
          composerTextareaMinHeight={56}
          initialMessage={pendingInitialSessionId === activeChatSessionId && selectedSession?.canWrite !== false ? pendingInitialMessage : null}
          initialDisplayText={pendingInitialSessionId === activeChatSessionId ? pendingInitialDisplayText : null}
          initialOptimisticMessage={pendingOptimisticMessage}
          focusMessageId={pendingFocusMessageId}
          onFocusMessageConsumed={() => setPendingFocusMessageId(null)}
          onOpenContextFile={handleOpenContextFile}
          onOpenContext={() => setContextPanelOpen(true)}
          onInitialMessageConsumed={() => {
            setPendingInitialMessage(null);
            setPendingInitialDisplayText(null);
          }}
          onInitialOptimisticMessageConsumed={(messageId) => {
            setPendingOptimisticMessage((message) => (
              message?.id === messageId ? null : message
            ));
          }}
          onSseStatusChange={activeChatSessionId ? setSseStatus : undefined}
          onSSEEvent={handleSSEEvent}
          onError={handleChatError}
          onCreateAndSend={handleCreateAndSend}
          onCreateNewSession={handleStartNewSession}
          onStreamingDone={() => {
            setPendingInitialMessage(null);
            setPendingInitialDisplayText(null);
          }}
          supportsVision={effectiveSupportsVision}
          contextWindowTokens={effectiveModelOption?.contextWindowTokens ?? null}
          model={selectedPromptModel}
          welcomeContent={(setInput) => (
            <WelcomeScreen
              onSuggestion={setInput}
              onAlertOperationsSetup={() => void handleAlertOperationsSetup()}
              alertOperationsBusy={installingSocWorkspace}
            />
          )}
          onComposerAddMenuOpenChange={(open) => {
            setShowAgentOptions(false);
            setShowWorkflowOptions(false);
            setShowSkillOptions(false);
            if (!open) return;
            setShowProjectOptions(false);
            setShowModelOptions(false);
            void loadComposerResources();
          }}
          composerAddMenuSlot={({ closeMenu, insertMention, insertReference }) => (
            <>
            <div className="relative" data-agent-selector>
              <button
                type="button"
                onClick={() => {
                  setShowAgentOptions((open) => !open);
                  setShowWorkflowOptions(false);
                  setShowSkillOptions(false);
                  setShowProjectOptions(false);
                  setShowModelOptions(false);
                }}
                className="group flex h-10 w-full items-center gap-2.5 rounded-card px-2 text-left text-xs font-medium text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
                title={t('agentPicker.title')}
                aria-label={t('chat.addMenu.agent')}
                aria-haspopup="menu"
                aria-expanded={showAgentOptions}
              >
                <span className="grid h-7 w-7 shrink-0 place-items-center text-fg-muted transition-colors group-hover:text-fg ">
                  <IconRobot aria-hidden="true" className="h-4 w-4" />
                </span>
                <span className="min-w-0 flex-1 truncate">{t('chat.addMenu.agent')}</span>
                <IconChevronRight aria-hidden="true" className={`h-4 w-4 shrink-0 text-fg-muted transition-transform ${showAgentOptions ? '-rotate-90' : ''}`} />
              </button>
              {showAgentOptions && (
                <div className="absolute bottom-0 left-[calc(100%+0.5rem)] z-[60] w-[300px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-card border border-line bg-surface shadow-overlay backdrop-blur-xl ">
                  <div className="flex min-h-12 items-center justify-between gap-3 border-b border-line px-3 py-2 ">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-fg ">{t('agentPicker.title')}</div>
                      <div
                        className="truncate text-xs text-fg-muted "
                        onPointerEnter={(event) => showSelectorTooltip(event.currentTarget, t('agentPicker.title'), [t('agentPicker.hint')])}
                        onMouseEnter={(event) => showSelectorTooltip(event.currentTarget, t('agentPicker.title'), [t('agentPicker.hint')])}
                        onMouseOver={(event) => showSelectorTooltip(event.currentTarget, t('agentPicker.title'), [t('agentPicker.hint')])}
                        onMouseLeave={() => setSelectorTooltip(null)}
                        onPointerLeave={() => setSelectorTooltip(null)}
                      >
                        {t('agentPicker.hint')}
                      </div>
                    </div>
                    <div className="inline-flex shrink-0 items-center rounded-control bg-subtle p-0.5 text-xs ">
                      {(['all', 'builtin', 'custom'] as AgentSourceFilter[]).map((filter) => (
                        <button
                          key={filter}
                          type="button"
                          onClick={() => setAgentSourceFilter(filter)}
                          className={`rounded-control px-2 py-1 transition-colors ${
                            agentSourceFilter === filter
                              ? 'bg-surface text-fg shadow-overlay '
                              : 'text-fg-muted hover:text-fg '
                          }`}
                        >
                          {t(`agentPicker.filter.${filter}`)}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="h-64 space-y-0.5 overflow-y-auto p-1.5">
                    {loadingAgents ? (
                      <div className="p-3 text-center text-xs text-fg-muted">{t('loading')}</div>
                    ) : filteredChatAgents.length > 0 ? (
                      filteredChatAgents.map((agent) => {
                        const displayName = getAgentDisplayName(agent, i18n.language);
                        const primaryDesc = getAgentDisplayDescription(agent, i18n.language) || t('smartAssistant');
                        return (
                          <button
                            key={agent.name}
                            type="button"
                            onClick={() => {
                              setShowAgentOptions(false);
                              insertMention(agent.name);
                              closeMenu();
                            }}
                            className={`w-full min-w-0 rounded-control border px-2.5 py-2 text-left transition-colors ${
                              selectedAgent === agent.name
                                ? 'border-info bg-info-soft text-fg shadow-sm '
                                : 'border-transparent text-fg-secondary hover:bg-hover '
                            }`}
                          >
                            <div className="flex min-w-0 items-center gap-2">
                              <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-control ${
                                selectedAgent === agent.name
                                  ? 'bg-surface text-fg-secondary shadow-overlay '
                                  : 'bg-subtle text-fg-muted '
                              }`}>
                                <IconRobot aria-hidden="true" className="h-4 w-4" />
                              </span>
                              <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg ">
                                {displayName}
                              </span>
                              <span className={`shrink-0 rounded-tag px-1.5 py-0.5 text-xs font-medium ${
                                agent.mode === 'primary'
                                  ? 'bg-subtle text-fg-secondary '
                                  : agent.native
                                    ? 'bg-subtle text-fg-secondary '
                                    : 'bg-data-soft text-data '
                              }`}>
                                {agent.mode === 'primary'
                                  ? t('agentPicker.badge.primary')
                                  : agent.native
                                    ? t('agentPicker.badge.builtin')
                                    : t('agentPicker.badge.custom')}
                              </span>
                              {primaryDesc && (
                                <span
                                  className="group relative rounded-tag p-0.5 transition-colors hover:bg-hover "
                                  onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                  onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                  onPointerEnter={(event) => showSelectorTooltip(event.currentTarget, displayName, [primaryDesc])}
                                  onMouseEnter={(event) => showSelectorTooltip(event.currentTarget, displayName, [primaryDesc])}
                                  onMouseOver={(event) => showSelectorTooltip(event.currentTarget, displayName, [primaryDesc])}
                                  onMouseLeave={() => setSelectorTooltip(null)}
                                  onPointerLeave={() => setSelectorTooltip(null)}
                                >
                                  <IconInfoCircle aria-hidden="true" className="h-4 w-4 text-fg-placeholder transition-colors group-hover:text-fg-muted " />
                                </span>
                              )}
                              {selectedAgent === agent.name && <IconCheck aria-hidden="true" className="h-4 w-4 shrink-0 text-info " />}
                            </div>
                          </button>
                        );
                      })
                    ) : (
                      <div className="p-3 text-center text-xs text-fg-muted">{t('noAgents')}</div>
                    )}
                  </div>
                </div>
              )}
            </div>
            <ComposerResourcePicker
              label={t('chat.addMenu.skills')}
              title={t('chat.addMenu.selectSkill')}
              emptyText={t('chat.addMenu.noSkills')}
              icon={IconBook}
              items={composerSkills.map((skill) => ({
                id: skill.name,
                name: skill.name,
                description: skill.description,
                badge: skill.category,
                source: skill.source === 'project' ? 'builtin' : 'custom',
              }))}
              loading={loadingComposerResources}
              open={showSkillOptions}
              onToggle={() => {
                setShowSkillOptions((open) => !open);
                setShowAgentOptions(false);
                setShowWorkflowOptions(false);
              }}
              onSelect={(skill) => {
                insertReference(skill.id, 'skill');
              }}
            />
            <ComposerResourcePicker
              label={t('chat.addMenu.workflows')}
              title={t('chat.addMenu.selectWorkflow')}
              emptyText={t('chat.addMenu.noWorkflows')}
              icon={WorkflowIcon}
              items={composerWorkflows.map((workflow) => ({
                id: workflow.id,
                name: getWorkflowDisplayName(workflow, i18n.language),
                description: workflow.description,
                source: workflow.source === 'project' ? 'builtin' : 'custom',
              }))}
              loading={loadingComposerResources}
              open={showWorkflowOptions}
              onToggle={() => {
                setShowWorkflowOptions((open) => !open);
                setShowAgentOptions(false);
                setShowSkillOptions(false);
              }}
              onSelect={(workflow) => {
                insertReference(workflow.id, 'workflow');
                setShowWorkflowOptions(false);
                closeMenu();
              }}
            />
            </>
          )}
          toolbarSlot={
            <div className="flex min-w-0 items-center gap-0.5">
              <div ref={executionModeSelectorRef} className="relative" data-execution-mode-selector>
                <button
                  type="button"
                  onClick={() => {
                    setShowExecutionModeOptions(!showExecutionModeOptions);
                    setShowAgentOptions(false);
                    setShowModelOptions(false);
                    setShowProjectOptions(false);
                  }}
                  className="flex h-7 items-center gap-1.5 rounded-control px-2 text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
                  title={t(`executionMode.options.${selectedExecutionMode}.description`)}
                  aria-label={t('executionMode.title')}
                  aria-haspopup="menu"
                  aria-expanded={showExecutionModeOptions}
                >
                  <ExecutionModeIcon mode={selectedExecutionMode} />
                  <span className="font-medium">
                    {t(`executionMode.options.${selectedExecutionMode}.label`)}
                  </span>
                  <IconChevronDown aria-hidden="true" className={`h-4 w-4 shrink-0 transition-transform ${showExecutionModeOptions ? 'rotate-180' : ''}`} />
                </button>
                {showExecutionModeOptions && (
                  <SessionComposerMenu
                    anchorRef={executionModeSelectorRef}
                    data-execution-mode-selector
                    role="menu"
                    aria-label={t('executionMode.title')}
                    header={(
                      <SessionComposerMenuHeader
                        title={t('executionMode.title')}
                        hint={t('executionMode.hint')}
                      />
                    )}
                    contentClassName="space-y-0.5 p-1.5"
                  >
                    {SESSION_EXECUTION_MODES.map((mode) => (
                      <SessionModeOption
                        key={mode}
                        role="menuitemradio"
                        aria-checked={selectedExecutionMode === mode}
                        selected={selectedExecutionMode === mode}
                        label={t(`executionMode.options.${mode}.label`)}
                        description={t(`executionMode.options.${mode}.description`)}
                        icon={<ExecutionModeIcon mode={mode} className="h-3.5 w-3.5 shrink-0" />}
                        onClick={() => handleSelectExecutionMode(mode)}
                      />
                    ))}
                  </SessionComposerMenu>
                )}
              </div>
              {!activeChatSessionId && (
              <div className="relative" data-project-selector>
                <button
                  type="button"
                  onClick={() => {
                    setShowProjectOptions((open) => !open);
                    setShowAgentOptions(false);
                    setShowExecutionModeOptions(false);
                    setShowModelOptions(false);
                  }}
                  className="flex h-7 max-w-[160px] min-w-0 items-center gap-1.5 rounded-control px-2 text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
                  title={t('projectPicker.title')}
                  aria-label={t('projectPicker.title')}
                  aria-expanded={showProjectOptions}
                >
                  <IconFolderCode aria-hidden="true" className="h-4 w-4 shrink-0" />
                  <span className="truncate font-medium">{selectedProjectContextLabel}</span>
                  <IconChevronDown aria-hidden="true" className={`h-4 w-4 shrink-0 transition-transform ${showProjectOptions ? 'rotate-180' : ''}`} />
                </button>
                {showProjectOptions && (
                  <div
                    className="absolute bottom-full left-0 z-50 mb-2 grid w-64 gap-0.5 rounded-card border border-line bg-surface p-1 shadow-overlay "
                    role="menu"
                    aria-label={t('projectPicker.title')}
                  >
                    {projectSessionGroups.map((group) => (
                      <button
                        key={group.id}
                        type="button"
                        role="menuitemradio"
                        aria-checked={selectedProjectId === group.id}
                        onClick={() => {
                          setSelectedProjectId(group.id);
                          setShowProjectOptions(false);
                        }}
                        className={`flex h-8 w-full items-center gap-2 rounded-control border px-2 text-left text-xs transition-colors ${
                          selectedProjectId === group.id
                            ? 'border-info bg-info-soft text-fg shadow-sm '
                            : 'border-transparent text-fg-secondary hover:bg-hover hover:text-fg '
                        }`}
                      >
                        <IconFolderCode aria-hidden="true" className={`h-4 w-4 shrink-0 ${selectedProjectId === group.id ? 'text-info ' : 'text-fg-muted '}`} />
                        <span className="min-w-0 flex-1 truncate">{group.label}</span>
                        {selectedProjectId === group.id && <IconCheck aria-hidden="true" className="h-4 w-4 shrink-0 text-info " />}
                      </button>
                    ))}
                    <div className="mx-2 my-0.5 border-t border-line " />
                    <button
                      type="button"
                      role="menuitemradio"
                      aria-checked={selectedProjectId === TASK_SESSION_GROUP_ID}
                      onClick={() => {
                        setSelectedProjectId(TASK_SESSION_GROUP_ID);
                        setShowProjectOptions(false);
                      }}
                      className={`flex h-8 w-full items-center gap-2 rounded-control border px-2 text-left text-xs transition-colors ${
                        selectedProjectId === TASK_SESSION_GROUP_ID
                          ? 'border-info bg-info-soft text-fg shadow-sm '
                          : 'border-transparent text-fg-secondary hover:bg-hover hover:text-fg '
                      }`}
                    >
                      <IconX aria-hidden="true" className={`h-4 w-4 shrink-0 ${selectedProjectId === TASK_SESSION_GROUP_ID ? 'text-info ' : 'text-fg-muted '}`} />
                      <span className="min-w-0 flex-1 truncate">{t('projectPicker.none')}</span>
                      {selectedProjectId === TASK_SESSION_GROUP_ID && <IconCheck aria-hidden="true" className="h-4 w-4 shrink-0 text-info " />}
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setShowProjectOptions(false);
                        handleOpenCreateProject();
                      }}
                      className="flex h-8 w-full items-center gap-2 rounded-control px-2 text-left text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
                    >
                      <IconFolderPlus aria-hidden="true" className="h-4 w-4 shrink-0 text-fg-muted " />
                      <span>{t('projectPicker.create')}</span>
                    </button>
                  </div>
                )}
              </div>
            )}
            </div>
          }
          centerToolbarSlot={
            <div className="flex items-center gap-1">
              <div ref={modelSelectorRef} className="relative" data-model-selector>
              <button
                type="button"
                onClick={() => {
                  setShowModelOptions(!showModelOptions);
                  setShowExecutionModeOptions(false);
                  setShowProjectOptions(false);
                  setShowAgentOptions(false);
                }}
                disabled={loadingProviders || loadingEnabledModels || chatModelOptions.length === 0}
                className="flex h-7 w-[132px] min-w-0 items-center gap-1.5 rounded-control px-2 text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
                title={selectedModelAuto
                  ? `${t('modelPicker.auto')}: ${autoStatusLabel}`
                  : selectedModelOption
                    ? `${selectedModelOption.providerName} / ${selectedModelOption.modelID}`
                    : t('modelPicker.empty')}
              >
                {selectedModelAuto
                  ? <IconSparkles aria-hidden="true" className="h-4 w-4 shrink-0" />
                  : <IconCpu aria-hidden="true" className="h-4 w-4 shrink-0" />}
                <span className="truncate font-medium">
                  {selectedModelAuto
                    ? t('modelPicker.auto')
                    : selectedModelOption?.label ?? (loadingProviders || loadingEnabledModels ? t('loading') : t('modelPicker.empty'))}
                </span>
                <IconChevronDown aria-hidden="true" className={`h-4 w-4 shrink-0 transition-transform ${showModelOptions ? 'rotate-180' : ''}`} />
              </button>
              {showModelOptions && (
                <SessionComposerMenu
                  anchorRef={modelSelectorRef}
                  data-model-selector
                  aria-label={t('modelPicker.title')}
                  header={(
                    <SessionComposerMenuHeader
                      title={t('modelPicker.title')}
                      hint={t('modelPicker.hint')}
                    />
                  )}
                  contentClassName="p-1.5"
                  footer={(
                    <div className="border-t border-line p-1.5 ">
                      <button
                        type="button"
                        onClick={() => {
                          setShowModelOptions(false);
                          setSelectorTooltip(null);
                          navigate('/models');
                        }}
                        className="flex w-full items-center justify-center gap-1.5 rounded-control px-2 py-1.5 text-xs font-medium text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
                      >
                        <IconPlus aria-hidden="true" className="h-4 w-4" />
                        {t('modelPicker.addModel')}
                      </button>
                    </div>
                  )}
                >
                    {loadingProviders || loadingEnabledModels ? (
                      <div className="p-3 text-center text-xs text-fg-muted">{t('loading')}</div>
                    ) : (
                      <>
                        <div className="mb-1 border-b border-line pb-1.5 ">
                          <button
                            type="button"
                            onClick={() => void handleSelectAutoModel()}
                            title={`${t('modelPicker.auto')}\n${autoStatusLabel}`}
                            disabled={!canSelectAuto}
                            className={`w-full rounded-control border px-2 py-1.5 text-left transition-colors disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled ${
                              selectedModelAuto
                                ? 'border-info bg-info-soft text-fg shadow-sm '
                                : 'border-transparent text-fg-secondary hover:bg-hover '
                            }`}
                          >
                            <div className="flex min-w-0 items-center gap-2">
                              <IconSparkles aria-hidden="true" className={`h-4 w-4 shrink-0 ${selectedModelAuto ? 'text-fg-secondary ' : 'text-fg-muted '}`} />
                              <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg ">
                                {t('modelPicker.auto')}
                              </span>
                              <span
                                className="group relative rounded-tag p-0.5 transition-colors hover:bg-hover "
                                onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                onPointerEnter={(event) => showSelectorTooltip(event.currentTarget, t('modelPicker.auto'), [autoStatusLabel])}
                                onMouseEnter={(event) => showSelectorTooltip(event.currentTarget, t('modelPicker.auto'), [autoStatusLabel])}
                                onMouseOver={(event) => showSelectorTooltip(event.currentTarget, t('modelPicker.auto'), [autoStatusLabel])}
                                onMouseLeave={() => setSelectorTooltip(null)}
                                onPointerLeave={() => setSelectorTooltip(null)}
                              >
                                <IconInfoCircle aria-hidden="true" className="h-4 w-4 text-fg-placeholder transition-colors group-hover:text-fg-muted " />
                              </span>
                              <IconCheck aria-hidden="true" className={`h-4 w-4 shrink-0 text-info ${selectedModelAuto ? '' : 'invisible'}`} />
                            </div>
                          </button>
                        </div>
                        {groupedChatModelOptions.length > 0 ? groupedChatModelOptions.map((group) => (
                        <div key={group.providerID} className="py-1 first:pt-0 last:pb-0">
                          <div className="sticky top-0 z-10 flex items-center justify-between gap-2 bg-surface px-1.5 py-1 text-xs font-semibold text-fg-muted backdrop-blur ">
                            <span className="min-w-0 truncate" title={group.providerName}>{group.providerName}</span>
                            <span className="shrink-0 rounded-tag bg-subtle px-1.5 py-0.5 text-xs text-fg-muted ">
                              {t('modelPicker.count', { count: group.models.length })}
                            </span>
                          </div>
                          <div className="space-y-0.5">
                            {group.models.map((option) => (
                              <button
                                key={option.key}
                                type="button"
                                onClick={() => void handleSelectModel(option)}
                                title={`${option.label}\n${option.providerName} / ${option.modelID}\n${option.pricingLabel}\n${option.contextLabel}`}
                                className={`w-full rounded-control border px-2 py-1.5 text-left transition-colors ${
                                  selectedModelOption?.key === option.key
                                    ? 'border-info bg-info-soft text-fg shadow-sm '
                                    : 'border-transparent text-fg-secondary hover:bg-hover '
                                }`}
                              >
                                <div className="flex min-w-0 items-center gap-2">
                                  <IconCpu aria-hidden="true" className={`h-4 w-4 shrink-0 ${selectedModelOption?.key === option.key ? 'text-fg-secondary ' : 'text-fg-muted '}`} />
                                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg ">{option.label}</span>
                                  {option.supportsVision === true && (
                                    <span className="shrink-0 rounded-tag bg-subtle px-1.5 py-0.5 text-xs font-medium text-fg-secondary ">
                                      {t('modelPicker.vision')}
                                    </span>
                                  )}
                                  <div className="ml-auto flex shrink-0 items-center gap-1">
                                    <span
                                      className="group relative rounded-tag p-0.5 transition-colors hover:bg-hover "
                                      onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                      onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                      onPointerEnter={(event) => showSelectorTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                                      onMouseEnter={(event) => showSelectorTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                                      onMouseOver={(event) => showSelectorTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                                      onMouseLeave={() => setSelectorTooltip(null)}
                                      onPointerLeave={() => setSelectorTooltip(null)}
                                    >
                                      <IconInfoCircle aria-hidden="true" className="h-4 w-4 text-fg-placeholder transition-colors group-hover:text-fg-muted " />
                                    </span>
                                  </div>
                                  <IconCheck aria-hidden="true" className={`h-4 w-4 shrink-0 text-info ${selectedModelOption?.key === option.key ? '' : 'invisible'}`} />
                                </div>
                              </button>
                            ))}
                          </div>
                        </div>
                        )) : (
                          <div className="p-3 text-center text-xs text-fg-muted">{t('modelPicker.empty')}</div>
                        )}
                      </>
                    )}
                </SessionComposerMenu>
              )}
              </div>
              {proPolicyEnabled && (
                <div ref={permissionModeSelectorRef} className="relative" data-permission-mode-selector>
                  <button
                    type="button"
                    onClick={() => setShowPermissionModeOptions((open) => !open)}
                    className="flex h-7 items-center gap-1.5 rounded-control px-2 text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
                    title={t('permissionMode.executionControlTitle')}
                  >
                    <IconShield aria-hidden="true" className="h-4 w-4 shrink-0" />
                    <span className="max-w-[116px] truncate font-medium">
                      {permissionModeLabels[currentPermissionMode]}
                    </span>
                    <IconChevronDown aria-hidden="true" className={`h-4 w-4 shrink-0 transition-transform ${showPermissionModeOptions ? 'rotate-180' : ''}`} />
                  </button>
                  {showPermissionModeOptions && (
                    <SessionComposerMenu
                      anchorRef={permissionModeSelectorRef}
                      data-permission-mode-selector
                      aria-label={t('permissionMode.executionControlTitle')}
                      contentClassName="space-y-3 p-1.5"
                    >
                      <section>
                        <div className="flex min-w-0 items-center justify-between gap-3 px-2.5 py-1.5">
                          <div
                            className="min-w-0 truncate text-xs font-semibold text-fg-muted "
                            title={t('permissionMode.runtimeTitle', '平台开发模式')}
                          >
                            {t('permissionMode.runtimeTitle', '平台开发模式')}
                          </div>
                          <button
                            type="button"
                            onClick={() => {
                              setShowPermissionModeOptions(false);
                              navigate('/settings/security-config');
                            }}
                            className="shrink-0 whitespace-nowrap text-xs font-medium text-info hover:underline "
                          >
                            {t('permissionMode.viewDetails')}
                          </button>
                        </div>
                        <div className="space-y-0.5">
                          {runtimeModeOptions.map(({ value: mode, label, description }) => (
                            <SessionModeOption
                              key={mode}
                              selected={currentRuntimeMode === mode}
                              label={label}
                              description={description}
                              onClick={() => {
                                void handleRuntimeModeChange(mode);
                                setShowPermissionModeOptions(false);
                              }}
                            />
                          ))}
                        </div>
                      </section>
                      <section className="border-t border-line pt-1.5 ">
                        <div
                          className="truncate px-2.5 py-1.5 text-xs font-semibold text-fg-muted "
                          title={t('permissionMode.networkTitle', '网络访问模式')}
                        >
                          {t('permissionMode.networkTitle', '网络访问模式')}
                        </div>
                        <div className="space-y-0.5">
                          {networkModeOptions.map(({ value: mode, label, description }) => (
                            <SessionModeOption
                              key={mode}
                              selected={currentNetworkMode === mode}
                              label={label}
                              description={description}
                              onClick={() => {
                                void handleNetworkModeChange(mode);
                                setShowPermissionModeOptions(false);
                              }}
                            />
                          ))}
                        </div>
                      </section>
                      <section className="border-t border-line pt-1.5 ">
                        <div
                          className="truncate px-2.5 py-1.5 text-xs font-semibold text-fg-muted "
                          title={t('permissionMode.title')}
                        >
                          {t('permissionMode.title')}
                        </div>
                        <div className="space-y-0.5">
                          {permissionModeOptions.map(({ value: mode, label, description }) => (
                            <SessionModeOption
                              key={mode}
                              selected={currentPermissionMode === mode}
                              label={label}
                              description={description}
                              onClick={() => {
                                void handlePermissionModeChange(mode);
                                setShowPermissionModeOptions(false);
                              }}
                            />
                          ))}
                        </div>
                      </section>
                    </SessionComposerMenu>
                  )}
                </div>
              )}
            </div>
          }
          />
        )}
        {contextPanelOpen && activeChatSessionId && (
          <>
            <div
              role="separator"
              aria-orientation="vertical"
              onPointerDown={handleResizeContextPanel}
              className="hidden w-1 flex-shrink-0 cursor-col-resize bg-selected transition-colors hover:bg-ai lg:block "
            />
            <aside
              className="fixed inset-0 z-50 h-full w-screen flex-shrink-0 overflow-hidden border-l border-line lg:static lg:z-auto lg:w-[var(--session-context-width)] "
              style={{ '--session-context-width': `${contextPanelWidth}px` } as React.CSSProperties}
            >
              <SessionContextPanel
                key={`${activeChatSessionId}:${contextResetVersion}`}
                sessionId={activeChatSessionId}
                snapshot={contextSnapshot?.sessionID === activeChatSessionId ? contextSnapshot : null}
                loading={contextLoading}
                loadingMore={contextLoadingMore}
                error={contextError}
                requestedResourceID={requestedContextResource?.sessionId === activeChatSessionId ? requestedContextResource.resourceID : null}
                onRequestedResourceConsumed={() => setRequestedContextResource(null)}
                onClose={() => {
                  setContextPanelOpen(false);
                  setRequestedContextResource(null);
                }}
                onRefresh={() => fetchSessionContext()}
                onLoadMore={() => fetchSessionContext(true)}
                onFocusMessage={(messageId) => setPendingFocusMessageId(messageId)}
              />
            </aside>
          </>
        )}
        </div>
      </div>

      {projectDialogMode && (
        <PageDialog onClose={projectSubmitting ? undefined : handleCloseProjectDialog} className="fixed inset-0 z-[70] flex items-center justify-center bg-overlay-backdrop px-6 backdrop-blur-[2px]">
          <div className="w-full max-w-modal overflow-hidden rounded-card border border-line bg-surface shadow-overlay ">
            <div className="border-b border-line px-4 py-[13px] ">
              <h3 className="text-sm font-semibold text-fg ">
                {projectDialogMode === 'create' ? t('projectDialog.createTitle') : t('projectDialog.renameTitle')}
              </h3>
            </div>
            <div className="space-y-3 px-4 py-4 text-xs text-fg-secondary ">
              <label className="block text-xs font-semibold text-fg-muted " htmlFor="session-project-name">
                {t('projectDialog.nameLabel')}
              </label>
              <input
                id="session-project-name"
                value={projectNameValue}
                onChange={(event) => {
                  setProjectNameValue(event.target.value);
                  setProjectNameManuallyEdited(true);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    if (
                      event.nativeEvent.isComposing
                      || (projectDialogMode === 'create'
                        && !projectWorktreeValue.trim()
                        && !folderBrowser?.path.trim())
                    ) {
                      return;
                    }
                    void handleSubmitProject();
                  }

                }}
                disabled={projectSubmitting}
                placeholder={t('projectDialog.namePlaceholder')}
                className="ui-input h-9 w-full rounded-card border border-line bg-surface px-2.5 text-xs text-fg transition-colors placeholder:text-fg focus:border-info"
                autoFocus
              />
              {projectDialogMode === 'create' && (
                <>
                  <label className="block pt-1 text-xs font-semibold text-fg-muted " htmlFor="session-project-worktree">
                    {t('projectDialog.folderLabel')}
                  </label>
                  <div className="flex gap-2">
                    <input
                      id="session-project-worktree"
                      value={projectWorktreeValue}
                      onChange={(event) => {
                        folderBrowserRequestIdRef.current += 1;
                        folderBrowserInputPathRef.current = null;
                        setFolderBrowserLoading(false);
                        setProjectWorktreeValue(event.target.value);
                        if (!projectNameManuallyEdited) {
                          setProjectNameValue(getPathBasename(event.target.value));
                        }
                      }}
                      disabled={projectSubmitting}
                      placeholder={t('projectDialog.folderPlaceholder')}
                      className="ui-input h-9 min-w-0 flex-1 rounded-card border border-line bg-surface px-2.5 font-mono text-xs text-fg transition-colors placeholder:text-fg focus:border-info"
                    />
                    <button
                      type="button"
                      onClick={() => void loadFolderBrowser(projectWorktreeValue)}
                      disabled={projectSubmitting || folderBrowserLoading}
                      className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-card border border-line px-3 text-xs font-medium text-fg-secondary transition-colors hover:bg-hover disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
                    >
                      {folderBrowserLoading ? <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <IconFolderOpen aria-hidden="true" className="h-4 w-4" />}
                      {t('projectDialog.chooseFolder')}
                    </button>
                  </div>
                  {folderBrowser && (
                    <div className="overflow-hidden rounded-control border border-line ">
                      <div className="flex items-center gap-1 border-b border-line bg-subtle px-2 py-1.5 ">
                        <button
                          type="button"
                          onClick={() => folderBrowser.parent && void loadFolderBrowser(folderBrowser.parent)}
                          disabled={!folderBrowser.parent || folderBrowserLoading}
                          className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1 text-fg-muted hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled "
                          title={t('projectDialog.parentFolder')}
                        >
                          <IconArrowUp aria-hidden="true" className="h-4 w-4" />
                        </button>
                        <span className="min-w-0 flex-1 truncate font-mono text-xs text-fg-muted" title={folderBrowser.path}>
                          {folderBrowser.path}
                        </span>
                        <button
                          type="button"
                          onClick={() => handleSelectProjectFolder(folderBrowser.path)}
                          className="rounded-tag bg-subtle px-2 py-1 text-xs font-medium text-fg hover:bg-hover "
                        >
                          {t('projectDialog.selectCurrentFolder')}
                        </button>
                      </div>
                      <div className="flex gap-1 overflow-x-auto border-b border-line px-2 py-1 ">
                        {folderBrowser.roots.map((root) => (
                          <button
                            key={root.path}
                            type="button"
                            onClick={() => void loadFolderBrowser(root.path)}
                            className="inline-flex shrink-0 items-center gap-1 rounded-tag px-1.5 py-1 text-xs text-fg-muted hover:bg-hover hover:text-fg "
                            title={root.path}
                          >
                            <IconServer aria-hidden="true" className="h-4 w-4" />
                            {root.name}
                          </button>
                        ))}
                      </div>
                      <div className="max-h-48 overflow-y-auto p-1">
                        {folderBrowser.entries.length > 0 ? folderBrowser.entries.map((entry) => (
                          <button
                            key={entry.path}
                            type="button"
                            onClick={() => void loadFolderBrowser(entry.path)}
                            className="flex w-full items-center gap-2 rounded-tag px-2 py-1.5 text-left text-xs text-fg-secondary hover:bg-hover "
                            title={entry.path}
                          >
                            <IconFolderOpen aria-hidden="true" className="h-4 w-4 shrink-0 text-fg-muted" />
                            <span className="truncate">{entry.name}</span>
                          </button>
                        )) : (
                          <div className="px-2 py-5 text-center text-xs text-fg-muted">
                            {t('projectDialog.noSubfolders')}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-[13px] ">
              <button
                type="button"
                onClick={handleCloseProjectDialog}
                disabled={projectSubmitting}
                className="h-8 min-w-[66px] rounded-card bg-subtle px-3 text-xs text-fg-secondary transition-colors hover:bg-hover disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                onClick={() => void handleSubmitProject()}
                disabled={projectSubmitting}
                className="inline-flex h-8 min-w-[66px] items-center justify-center rounded-card bg-action px-3 text-xs font-medium text-action-fg transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
              >
                {projectSubmitting ? <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : t('save')}
              </button>
            </div>
          </div>
        </PageDialog>
      )}

      {projectPendingDelete && (
        <PageDialog onClose={projectDeleting ? undefined : () => setProjectPendingDelete(null)} className="fixed inset-0 z-[70] flex items-center justify-center bg-overlay-backdrop px-6 backdrop-blur-[2px]">
          <div className="w-full max-w-modal overflow-hidden rounded-card border border-line bg-surface shadow-overlay ">
            <div className="border-b border-line px-4 py-[13px] ">
              <h3 className="text-sm font-semibold text-fg ">
                {t('projectDialog.deleteTitle')}
              </h3>
            </div>
            <div className="px-4 py-4 text-xs text-fg-secondary ">
              {t('projectDialog.deleteDescription', { project: projectPendingDelete.label })}
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-[13px] ">
              <button
                type="button"
                onClick={() => setProjectPendingDelete(null)}
                disabled={projectDeleting}
                className="h-8 min-w-[66px] rounded-card bg-subtle px-3 text-xs text-fg-secondary transition-colors hover:bg-hover disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteProject()}
                disabled={projectDeleting}
                className="inline-flex h-8 min-w-[66px] items-center justify-center gap-1.5 rounded-control bg-action-danger px-3 text-xs font-medium text-action-fg transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled"
              >
                {projectDeleting ? <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <IconTrash aria-hidden="true" className="h-4 w-4" />}
                {t('projectDialog.confirmDelete')}
              </button>
            </div>
          </div>
        </PageDialog>
      )}

      {selectorTooltip && (
        <div
          role="tooltip"
          className="pointer-events-none fixed z-[80] w-56 -translate-x-full -translate-y-1/2 rounded-control border border-line bg-surface px-3 py-2 text-xs text-fg-secondary shadow-md "
          style={{ left: selectorTooltip.x, top: selectorTooltip.y }}
        >
          <div className="mb-0.5 font-semibold text-fg ">{selectorTooltip.title}</div>
          {selectorTooltip.lines.map((line, index) => (
            <div key={`${selectorTooltip.title}-${index}`} className={index === 0 ? '' : 'mt-1 break-all text-fg-muted '}>
              {line}
            </div>
          ))}
          <div className="absolute left-full top-1/2 -translate-y-1/2 border-4 border-transparent border-l-line " />
        </div>
      )}

      {suiteInstallProgress && (
        <SuiteInstallProgressPanel
          progress={suiteInstallProgress}
          language={i18n.language}
          onClose={() => setSuiteInstallProgress(null)}
        />
      )}

      {/* Three-dot dropdown — positioned in the viewport outside the sidebar. */}
      {openMenuSessionId && menuAnchor && (() => {
        const sid = openMenuSessionId;
        const session = sessions.find(s => s.id === sid);
        if (!session) return null;
        const currentProjectId = session.effectiveProjectID || session.projectID;
        const showMovePicker = movePickerSessionId === session.id;
        const moveTargetProjects = projects.filter((project) => (
          project.id.startsWith('prj_')
          && project.canWrite !== false
          && project.pathStatus !== 'missing'
          && project.pathStatus !== 'unreadable'
        ));
        return (
          <SessionActionsMenu anchor={menuAnchor} width={showMovePicker ? 220 : 132}>
            {showMovePicker ? (
              <>
                <div className="flex h-8 items-center gap-1 border-b border-line px-1 pb-1 ">
                  <button
                    type="button"
                    onClick={() => setMovePickerSessionId(null)}
                    className="ui-btn-icon inline-flex items-center justify-center grid   shrink-0 place-items-center rounded-control text-fg-muted transition-colors hover:bg-hover hover:text-fg "
                    aria-label={t('moveToProjectBack')}
                  >
                    <IconChevronLeft aria-hidden="true" className="h-4 w-4" />
                  </button>
                  <span className="min-w-0 truncate text-xs font-semibold text-fg ">
                    {t('moveToProjectTitle')}
                  </span>
                </div>
                <div className="session-sidebar-scrollbar min-h-0 max-h-56 overflow-y-auto py-0.5" role="menu" aria-label={t('moveToProjectTitle')}>
                  {moveTargetProjects.length === 0 ? (
                    <div className="px-2 py-3 text-center text-xs text-fg-muted ">
                      {t('moveToProjectEmpty')}
                    </div>
                  ) : moveTargetProjects.map((project) => {
                    const isCurrent = project.id === currentProjectId;
                    const isMovingHere = (
                      movingSessionId === session.id
                      && movingTargetProjectId === project.id
                    );
                    return (
                      <button
                        key={project.id}
                        type="button"
                        role="menuitem"
                        onClick={() => void handleMoveSessionToProject(session.id, project)}
                        disabled={isCurrent || Boolean(movingSessionId)}
                        title={project.worktree}
                        className="flex min-h-[34px] w-full items-center gap-2 rounded-control px-2 py-1.5 text-left text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg disabled:cursor-default disabled:bg-disabled disabled:text-fg-disabled "
                      >
                        <IconFolderCode aria-hidden="true" className="h-4 w-4 shrink-0 text-fg-muted " />
                        <span className="min-w-0 flex-1 truncate">{getProjectLabel(project)}</span>
                        {isCurrent && <IconCheck aria-hidden="true" className="h-4 w-4 shrink-0 text-info" />}
                        {!isCurrent && isMovingHere && <IconLoader2 aria-hidden="true" className="h-4 w-4 shrink-0 animate-spin text-info" />}
                      </button>
                    );
                  })}
                </div>
              </>
            ) : (
              <>
            <button
              onClick={(e) => { e.stopPropagation(); handleStartRename(session.id, session.title); setOpenMenuSessionId(null); setMenuAnchor(null); }}
              className="flex h-[30px] w-full items-center gap-2 rounded-control px-2 text-left text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg "
            >
              <IconEdit aria-hidden="true" className="w-4 h-4" />
              <span>{t('rename')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); void handleDownloadSession(session.id, session.title); setOpenMenuSessionId(null); setMenuAnchor(null); }}
              disabled={downloadingSessionId === session.id}
              className="flex h-[30px] w-full items-center gap-2 rounded-control px-2 text-left text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
            >
              <IconDownload aria-hidden="true" className="w-4 h-4" />
              <span>{t('downloadJson')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); setMenuAnchor(null); void handleShareSession(session.id, !session.isShared); }}
              disabled={session.canWrite === false}
              className="flex h-[30px] w-full items-center gap-2 rounded-control px-2 text-left text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
            >
              <IconShare aria-hidden="true" className="w-4 h-4" />
              <span>{session.isShared ? t('unshareAction') : t('shareAction')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setMovePickerSessionId(session.id); }}
              disabled={session.canWrite === false || runningSessionIds.has(session.id)}
              title={runningSessionIds.has(session.id) ? t('moveToProjectRunning') : undefined}
              className="flex h-[30px] w-full items-center gap-2 rounded-control px-2 text-left text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
            >
              <IconFolderOpen aria-hidden="true" className="h-4 w-4" />
              <span className="truncate">{t('moveToProjectAction')}</span>
            </button>
            <div className="mx-2 my-0.5 border-t border-line " />
            <button
              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); setMenuAnchor(null); void handleArchiveSession(session.id); }}
              disabled={session.canDelete === false}
              className="flex h-[30px] w-full items-center gap-2 rounded-control px-2 text-left text-xs text-danger transition-colors hover:bg-danger-soft hover:text-danger disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
            >
              <IconArchive aria-hidden="true" className="w-4 h-4" />
              <span>{t('archiveAction')}</span>
            </button>
              </>
            )}
          </SessionActionsMenu>
        );
      })()}
    </div>
  );
}

// ── Welcome Screen (shown when no messages) ──

function WelcomeScreen({
  onSuggestion,
  onAlertOperationsSetup,
  alertOperationsBusy,
}: {
  onSuggestion: (text: string) => void;
  onAlertOperationsSetup: () => void;
  alertOperationsBusy: boolean;
}) {
  const { t } = useTranslation('session');
  return (
    <div className="w-full max-w-[660px] px-9 text-center">
      <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-pill bg-subtle text-fg shadow-overlay ">
        <IconSparkles aria-hidden="true" className="h-8 w-8" stroke={2} />
      </div>
      <h3 className="text-title-page font-semibold tracking-[-0.01em] text-fg ">{t('welcome.title')}</h3>
      <p className="mt-3 text-sm text-fg-secondary ">{t('welcome.description')}</p>

      <div className="mt-6 flex flex-wrap justify-center gap-2.5">
        <button
          onClick={onAlertOperationsSetup}
          disabled={alertOperationsBusy}
          className="inline-flex h-11 items-center gap-2.5 rounded-card border border-line bg-subtle px-4 text-sm font-semibold text-fg-secondary transition-colors hover:border-line-control hover:bg-surface hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
        >
          {alertOperationsBusy ? (
            <IconLoader2 aria-hidden="true" className="h-5 w-5 animate-spin text-fg-secondary " />
          ) : (
            <IconShield aria-hidden="true" className="h-5 w-5 text-fg-secondary " />
          )}
          <span>{t('welcome.alertOperations')}</span>
        </button>
        <button
          onClick={() => onSuggestion(t('welcome.threatHuntingSuggestion'))}
          className="inline-flex h-11 items-center gap-2.5 rounded-card border border-line bg-subtle px-4 text-sm font-semibold text-fg-secondary transition-colors hover:border-line-control hover:bg-surface hover:text-fg "
        >
          <IconSearch aria-hidden="true" className="h-5 w-5 text-brand" />
          <span>{t('welcome.threatHunting')}</span>
        </button>
        <button
          onClick={() => onSuggestion(t('welcome.incidentResponseSuggestion'))}
          className="inline-flex h-11 items-center gap-2.5 rounded-card border border-line bg-subtle px-4 text-sm font-semibold text-fg-secondary transition-colors hover:border-line-control hover:bg-surface hover:text-fg "
        >
          <IconAlertTriangle aria-hidden="true" className="h-5 w-5 text-warning" />
          <span>{t('welcome.incidentResponse')}</span>
        </button>
      </div>
    </div>
  );
}
