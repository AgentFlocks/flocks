import { IconCheck, IconX } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';

import type { ToolState } from '@/types';

export type TodoSummaryEntry = {
  id?: string;
  content: string;
  status?: string;
  activeForm?: string;
};

type TodoTranslator = (key: string) => string;

function isTodoSummaryEntry(value: unknown): value is TodoSummaryEntry {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.content === 'string';
}

export function readTodoEntries(value: unknown): TodoSummaryEntry[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(isTodoSummaryEntry)
    .map((todo) => ({
      id: typeof todo.id === 'string' ? todo.id : undefined,
      content: todo.content.trim(),
      status: typeof todo.status === 'string' ? todo.status : undefined,
      activeForm: typeof todo.activeForm === 'string' ? todo.activeForm : undefined,
    }))
    .filter((todo) => todo.content.length > 0);
}

export function pickTodoEntries(...candidates: unknown[]): TodoSummaryEntry[] {
  for (const candidate of candidates) {
    const todos = readTodoEntries(candidate);
    if (todos.length > 0) return todos;
  }
  return [];
}

function getTodoActionLabel(action: unknown): string {
  if (action === 'read') return 'Read todos';
  if (action === 'write') return 'Update todos';
  return 'Todos';
}

export function buildTodoSummary(state: Partial<ToolState>, t?: TodoTranslator): string {
  const metadata = state.metadata ?? {};
  const currentTodos = pickTodoEntries(metadata.newTodos, metadata.todos, state.input?.todos);
  if (currentTodos.length === 0) return getTodoActionLabel(state.input?.action);
  const totalCount = currentTodos.length;
  const terminalCount = currentTodos.filter(
    (todo) => todo.status === 'completed' || todo.status === 'cancelled',
  ).length;
  const inProgressCount = currentTodos.filter((todo) => todo.status === 'in_progress').length;
  const hasCancelled = currentTodos.some((todo) => todo.status === 'cancelled');

  let summary = terminalCount === totalCount
    ? hasCancelled
      ? `${t?.('chat.tool.todoSummary.done') ?? 'Done'} ${terminalCount}/${totalCount}`
      : `${t?.('chat.tool.todoSummary.completed') ?? 'Completed'} ${terminalCount}/${totalCount}`
    : `${t?.('chat.tool.todoSummary.progress') ?? 'Progress'} ${terminalCount}/${totalCount}`;

  if (inProgressCount > 0 && terminalCount < totalCount) {
    summary += ` · ${t?.('chat.tool.todoSummary.inProgress') ?? 'In progress'} ${inProgressCount}`;
  }
  return summary;
}

function statusLabel(status: string | undefined, t: TodoTranslator): string {
  switch (status) {
    case 'completed': return t('chat.tool.todoStatus.completed');
    case 'in_progress': return t('chat.tool.todoStatus.inProgress');
    case 'cancelled': return t('chat.tool.todoStatus.cancelled');
    case 'pending': return t('chat.tool.todoStatus.pending');
    default: return status || 'pending';
  }
}

function statusIcon(status: string | undefined): React.ReactNode {
  switch (status) {
    case 'completed':
      return (
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-success-soft text-success">
          <IconCheck aria-hidden="true" className="h-4 w-4" stroke={2} />
        </span>
      );
    case 'in_progress':
      return (
        <span className="flex h-4 w-4 items-center justify-center rounded-full border border-info bg-surface">
          <span className="h-1.5 w-1.5 rounded-full bg-info" />
        </span>
      );
    case 'cancelled':
      return (
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-selected text-fg-muted">
          <IconX aria-hidden="true" className="h-4 w-4" stroke={2} />
        </span>
      );
    default:
      return <span className="h-4 w-4 rounded-full border border-line-control bg-surface" />;
  }
}

function textClass(status: string | undefined): string {
  switch (status) {
    case 'completed': return 'text-fg-muted';
    case 'in_progress': return 'font-medium text-fg dark:text-fg';
    case 'cancelled': return 'text-fg-muted line-through decoration-line-control';
    default: return 'text-fg-secondary dark:text-fg-secondary';
  }
}

function labelClass(status: string | undefined): string {
  switch (status) {
    case 'completed': return 'text-success';
    case 'in_progress': return 'text-info';
    default: return 'text-fg-muted';
  }
}

export function TodoList({ items }: { items: TodoSummaryEntry[] }) {
  const { t } = useTranslation('session');
  return (
    <div className="divide-y divide-line dark:divide-line">
      {items.map((todo, index) => (
        <div
          key={todo.id || index}
          className="grid grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-2 py-1.5 text-xs first:pt-0 last:pb-0"
        >
          <span className="mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center">
            {statusIcon(todo.status)}
          </span>
          <span className={`min-w-0 leading-5 ${textClass(todo.status)}`}>
            {todo.activeForm && todo.status === 'in_progress' ? todo.activeForm : todo.content}
          </span>
          <span className={`flex-shrink-0 whitespace-nowrap leading-5 ${labelClass(todo.status)}`}>
            {statusLabel(todo.status, t)}
          </span>
        </div>
      ))}
    </div>
  );
}
