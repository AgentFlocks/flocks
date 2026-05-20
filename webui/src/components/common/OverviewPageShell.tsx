import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';
import PageHeader, { type PageHeaderProps } from './PageHeader';

export interface OverviewPageShellProps extends PageHeaderProps {
  children: ReactNode;
  banner?: ReactNode;
  className?: string;
  contentClassName?: string;
}

/** Standard layout for list / config / management pages. */
export default function OverviewPageShell({
  children,
  banner,
  className,
  contentClassName,
  ...headerProps
}: OverviewPageShellProps) {
  return (
    <div className={cn('flex h-full flex-col', className)}>
      <div className="flex-shrink-0 pt-1">
        <PageHeader {...headerProps} />
        {banner}
      </div>
      <div
        className={cn(
          'flex-1 overflow-auto pb-2 space-y-4',
          contentClassName,
        )}
        style={{ scrollbarGutter: 'stable' }}
      >
        {children}
      </div>
    </div>
  );
}
