import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, FolderGit2, MessageSquare } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import client from '@/api/client';
import { sessionApi } from '@/api/session';
import type { Session } from '@/types';

const TASK_SESSION_GROUP_ID = 'tasks';
const SESSION_PAGE_SIZE = 6;
const WORKBENCH_NAVIGATION_REFRESH_EVENT = 'flocks:workbench-navigation-refresh';

type ProjectSummary = {
  id: string;
  worktree: string;
  name?: string | null;
  isDefault?: boolean;
  sessionCount?: number;
  lastActivityAt?: number | null;
};

function projectLabel(project: ProjectSummary): string {
  const explicitName = project.name?.trim();
  if (explicitName) return explicitName;
  const normalizedPath = project.worktree.replace(/[\\/]+$/, '');
  return normalizedPath.split(/[\\/]/).pop() || project.worktree;
}

function SessionLink({
  session,
  nested = false,
  selected,
  onSelect,
  onNavigate,
}: {
  session: Session;
  nested?: boolean;
  selected: boolean;
  onSelect: () => void;
  onNavigate: () => void;
}) {
  return (
    <Link
      to={`/sessions?session=${encodeURIComponent(session.id)}`}
      onClick={() => {
        onSelect();
        onNavigate();
      }}
      className={`flex h-8 min-w-0 items-center gap-2 rounded-lg pr-2 text-xs transition-colors ${
        nested ? 'pl-7' : 'pl-3'
      } ${
        selected
          ? 'bg-white font-semibold text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
          : 'text-zinc-500 hover:bg-white/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50'
      }`}
      title={session.title}
    >
      <MessageSquare className="h-3.5 w-3.5 shrink-0 text-zinc-400 dark:text-zinc-500" />
      <span className="min-w-0 flex-1 truncate">{session.title}</span>
    </Link>
  );
}

export default function AIWorkbenchNavigation({
  collapsed,
  onNavigate,
}: {
  collapsed: boolean;
  onNavigate: () => void;
}) {
  const location = useLocation();
  const { t } = useTranslation('session');
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [collapsedProjectIds, setCollapsedProjectIds] = useState<Set<string>>(() => new Set());
  const [projectsCollapsed, setProjectsCollapsed] = useState(false);
  const [tasksCollapsed, setTasksCollapsed] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const sessionParam = new URLSearchParams(location.search).get('session');
  const [activeSessionId, setActiveSessionId] = useState<string | null>(sessionParam);

  useEffect(() => {
    if (sessionParam) setActiveSessionId(sessionParam);
  }, [sessionParam]);

  useEffect(() => {
    const refresh = () => setRefreshVersion((version) => version + 1);
    window.addEventListener(WORKBENCH_NAVIGATION_REFRESH_EVENT, refresh);
    return () => window.removeEventListener(WORKBENCH_NAVIGATION_REFRESH_EVENT, refresh);
  }, []);

  useEffect(() => {
    if (location.pathname === '/sessions') {
      return undefined;
    }
    let cancelled = false;
    const loadNavigation = async () => {
      try {
        const response = await client.get('/api/project');
        const nextProjects = Array.isArray(response.data)
          ? response.data.filter((project: ProjectSummary) => (
            project.id !== TASK_SESSION_GROUP_ID && !project.isDefault
          ))
          : [];
        const projectIds = [TASK_SESSION_GROUP_ID, ...nextProjects.map((project) => project.id)];
        const sessionGroups = await Promise.all(projectIds.map((projectID) => sessionApi.list({
          view: 'list',
          manager: true,
          roots: true,
          limit: SESSION_PAGE_SIZE,
          projectID,
        })));
        if (cancelled) return;
        setProjects(nextProjects);
        setSessions(sessionGroups.flatMap((group) => Array.isArray(group) ? group : []));
      } catch {
        if (!cancelled) {
          setProjects([]);
          setSessions([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void loadNavigation();
    return () => {
      cancelled = true;
    };
  }, [location.pathname, refreshVersion]);

  const sessionsByProject = useMemo(() => {
    const grouped = new Map<string, Session[]>();
    sessions.forEach((session) => {
      const projectId = session.effectiveProjectID || session.projectID || TASK_SESSION_GROUP_ID;
      const group = grouped.get(projectId) ?? [];
      group.push(session);
      grouped.set(projectId, group);
    });
    return grouped;
  }, [sessions]);
  const sortedProjects = useMemo(() => [...projects].sort((left, right) => {
    const activityDelta = (right.lastActivityAt ?? 0) - (left.lastActivityAt ?? 0);
    return activityDelta || projectLabel(left).localeCompare(projectLabel(right));
  }), [projects]);
  const taskSessions = sessionsByProject.get(TASK_SESSION_GROUP_ID) ?? [];

  if (collapsed) {
    return (
      <Link
        to="/sessions"
        onClick={onNavigate}
        title={t('managementTitle')}
        className={`flex justify-center rounded-lg p-2.5 transition-colors ${
          location.pathname === '/sessions'
            ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
            : 'text-zinc-600 hover:bg-white/60 dark:text-zinc-400 dark:hover:bg-zinc-900'
        }`}
      >
        <MessageSquare className="h-5 w-5" />
      </Link>
    );
  }

  if (location.pathname === '/sessions') return null;

  const toggleProject = (projectId: string) => {
    setCollapsedProjectIds((current) => {
      const next = new Set(current);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  };

  return (
    <div className="space-y-3" data-testid="ai-workbench-navigation">
      <section>
        <button
          type="button"
          onClick={() => setProjectsCollapsed((value) => !value)}
          className="flex h-7 w-full items-center gap-1 rounded-lg px-2 text-left text-xs font-semibold text-zinc-500 transition-colors hover:bg-white/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50"
          aria-expanded={!projectsCollapsed}
        >
          <span>{t('projectsSection')}</span>
          <span className="font-normal tabular-nums text-zinc-400">({projects.length})</span>
          {projectsCollapsed
            ? <ChevronRight className="ml-auto h-3.5 w-3.5" />
            : <ChevronDown className="ml-auto h-3.5 w-3.5" />}
        </button>
        {!projectsCollapsed && (
          <div className="mt-0.5 space-y-0.5">
            {sortedProjects.map((project) => {
              const projectSessions = sessionsByProject.get(project.id) ?? [];
              const projectCollapsed = collapsedProjectIds.has(project.id);
              return (
                <div key={project.id}>
                  <button
                    type="button"
                    onClick={() => toggleProject(project.id)}
                    className="flex h-8 w-full min-w-0 items-center gap-2 rounded-lg px-3 text-left text-xs font-medium text-zinc-600 transition-colors hover:bg-white/60 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-900 dark:hover:text-zinc-50"
                    title={project.worktree}
                    aria-expanded={!projectCollapsed}
                  >
                    <FolderGit2 className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
                    <span className="min-w-0 flex-1 truncate">{projectLabel(project)}</span>
                    <span className="text-[10px] tabular-nums text-zinc-400">
                      {project.sessionCount ?? projectSessions.length}
                    </span>
                    {projectCollapsed
                      ? <ChevronRight className="h-3 w-3 shrink-0 text-zinc-400" />
                      : <ChevronDown className="h-3 w-3 shrink-0 text-zinc-400" />}
                  </button>
                  {!projectCollapsed && projectSessions.map((session) => (
                    <SessionLink
                      key={session.id}
                      session={session}
                      nested
                      selected={activeSessionId === session.id}
                      onSelect={() => setActiveSessionId(session.id)}
                      onNavigate={onNavigate}
                    />
                  ))}
                  {!projectCollapsed && !loading && projectSessions.length === 0 && (
                    <div className="py-1 pl-7 pr-2 text-[11px] text-zinc-400">
                      {t('noProjectSessions')}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section>
        <button
          type="button"
          onClick={() => setTasksCollapsed((value) => !value)}
          className="flex h-7 w-full items-center gap-1 rounded-lg px-2 text-left text-xs font-semibold text-zinc-500 transition-colors hover:bg-white/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50"
          aria-expanded={!tasksCollapsed}
        >
          <span>{t('tasksSection')}</span>
          <span className="font-normal tabular-nums text-zinc-400">({taskSessions.length})</span>
          {tasksCollapsed
            ? <ChevronRight className="ml-auto h-3.5 w-3.5" />
            : <ChevronDown className="ml-auto h-3.5 w-3.5" />}
        </button>
        {!tasksCollapsed && (
          <div className="mt-0.5 space-y-0.5">
            {taskSessions.map((session) => (
              <SessionLink
                key={session.id}
                session={session}
                selected={activeSessionId === session.id}
                onSelect={() => setActiveSessionId(session.id)}
                onNavigate={onNavigate}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
