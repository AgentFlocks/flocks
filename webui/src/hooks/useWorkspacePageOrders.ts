import { useEffect, useMemo, useState } from 'react';
import {
  applyOrderByKey,
  applyWorkspacePageOrder,
  readNavItemOrder,
  readWorkspacePageOrder,
  WORKSPACE_PAGE_ORDER_CHANGED_EVENT,
} from '@/utils/workspaceNavOrder';

export interface WorkspacePageOrders {
  /** Increments whenever an order is saved in this tab. */
  version: number;
  read: (workspaceId: string) => string[];
  apply: <T extends { id: string }>(workspaceId: string, pages: T[]) => T[];
  /** Same for built-in sidebar entries of one group, keyed by `keyOf(item)`. */
  applyNav: <T>(scopeId: string, items: T[], keyOf: (item: T) => string) => T[];
}

/**
 * Exposes the per-browser workspace page order and re-renders callers when a
 * new order is saved (for example after dragging a SOC page in the sidebar).
 */
export function useWorkspacePageOrders(): WorkspacePageOrders {
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const handleChange = () => setVersion((current) => current + 1);
    window.addEventListener(WORKSPACE_PAGE_ORDER_CHANGED_EVENT, handleChange);
    return () => window.removeEventListener(WORKSPACE_PAGE_ORDER_CHANGED_EVENT, handleChange);
  }, []);

  return useMemo(() => ({
    version,
    read: (workspaceId: string) => readWorkspacePageOrder(workspaceId),
    apply: <T extends { id: string }>(workspaceId: string, pages: T[]) => (
      applyWorkspacePageOrder(pages, readWorkspacePageOrder(workspaceId))
    ),
    applyNav: <T>(scopeId: string, items: T[], keyOf: (item: T) => string) => (
      applyOrderByKey(items, keyOf, readNavItemOrder(scopeId))
    ),
  }), [version]);
}
