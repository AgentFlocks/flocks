import type { ReactNode } from 'react';
import { IconMessage } from '@tabler/icons-react';
import type { ChatGuideAction } from './ChatGuideDock';
import GuideInfoIcon from './GuideInfoIcon';

export interface GuidedCreateGroup {
  title: string;
  actions: ChatGuideAction[];
}

export function normalizeGuidedCreateActions(value: unknown, group?: string): ChatGuideAction[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const raw = item as Record<string, unknown>;
      const label = String(raw.label ?? '').trim();
      const description = String(raw.description ?? '').trim();
      const prompt = String(raw.prompt ?? '').trim();
      const rawGroup = String(raw.group ?? '').trim();
      if (!label || !prompt) return null;
      return {
        label,
        description: description || prompt,
        prompt,
        ...((rawGroup || group) ? { group: rawGroup || group } : {}),
      };
    })
    .filter((item): item is ChatGuideAction => item !== null);
}

export function buildGuidedCreateGroups(groups: Array<{ title: string; actions: unknown }>): GuidedCreateGroup[] {
  return groups
    .map((group) => ({
      title: group.title,
      actions: normalizeGuidedCreateActions(group.actions, group.title),
    }))
    .filter((group) => group.actions.length > 0);
}

interface GuidedCreatePanelProps {
  emptyTitle?: string;
  icon?: ReactNode;
  title: string;
  description: string;
  groups: GuidedCreateGroup[];
  scrollTestId?: string;
  onStartPrompt: (prompt: string, label: string) => void;
}

export default function GuidedCreatePanel({
  emptyTitle,
  icon,
  title,
  description,
  groups,
  scrollTestId,
  onStartPrompt,
}: GuidedCreatePanelProps) {
  const visibleGroups = groups
    .map((group) => ({
      ...group,
      actions: group.actions.filter((action) => action.label && action.prompt),
    }))
    .filter((group) => group.actions.length > 0);

  return (
    <div className="flex min-h-[420px] w-full flex-col items-center justify-center px-5 py-8">
      {emptyTitle && (
        <p className="mb-8 text-center text-sm font-medium text-fg-muted">
          {emptyTitle}
        </p>
      )}
      <div className="flex max-h-[min(560px,calc(100vh-260px))] w-full max-w-[420px] flex-col overflow-hidden rounded-xl border border-line bg-surface px-5 py-5 text-center shadow-sm">
        <div className="flex-shrink-0">
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl border border-ai bg-ai-soft text-ai">
            {icon ?? <IconMessage aria-hidden="true" className="h-5 w-5" />}
          </div>
          <h3 className="mt-4 text-sm font-semibold text-fg">
            {title}
          </h3>
          <p className="mx-auto mt-2 max-w-[300px] text-xs leading-relaxed text-fg-muted">
            {description}
          </p>
        </div>
        <div
          data-testid={scrollTestId}
          className="mt-4 min-h-0 space-y-4 overflow-y-auto pr-1 text-left [scrollbar-width:thin] [scrollbar-color:var(--border-control)_transparent]"
        >
          {visibleGroups.map((group) => (
            <GuidedCreateSection
              key={group.title}
              title={group.title}
              actions={group.actions}
              onStartPrompt={onStartPrompt}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function GuidedCreateSection({
  title,
  actions,
  onStartPrompt,
}: {
  title: string;
  actions: ChatGuideAction[];
  onStartPrompt: (prompt: string, label: string) => void;
}) {
  return (
    <section>
      <h4 className="mb-2 text-xs font-semibold text-fg-muted">{title}</h4>
      <div
        data-testid={`guided-create-section-${title}`}
        className="flex flex-col gap-1.5"
      >
        {actions.map((action) => (
          <div
            key={action.label}
            className="group flex h-8 w-full items-center justify-between gap-3 rounded-lg border border-line bg-surface px-3 text-left text-xs font-semibold text-fg-secondary transition-colors hover:border-ai hover:bg-ai-soft hover:text-ai"
          >
            <button
              type="button"
              onClick={() => onStartPrompt(action.prompt, action.label)}
              className="min-w-0 flex-1 truncate text-left"
            >
              {action.label}
            </button>
            <GuideInfoIcon
              label={action.label}
              description={action.description}
              className="group-hover:text-ai"
            />
          </div>
        ))}
      </div>
    </section>
  );
}
