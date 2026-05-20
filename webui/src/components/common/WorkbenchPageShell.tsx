import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';

export interface WorkbenchPageShellProps {
  children: ReactNode;
  topBar?: ReactNode;
  className?: string;
}

/** Full-height workbench layout (chat, editors, canvases). */
export default function WorkbenchPageShell({
  children,
  topBar,
  className,
}: WorkbenchPageShellProps) {
  return (
    <div className={cn('flex h-full min-h-0 flex-col bg-surface', className)}>
      {topBar}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
    </div>
  );
}
