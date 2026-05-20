import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';

export interface PageHeaderProps {
  title: string;
  description?: string;
  action?: ReactNode;
  secondaryAction?: ReactNode;
  icon?: ReactNode;
  status?: ReactNode;
  toolbar?: ReactNode;
  className?: string;
}

export default function PageHeader({
  title,
  description,
  action,
  secondaryAction,
  icon,
  status,
  toolbar,
  className,
}: PageHeaderProps) {
  return (
    <header className={cn('mb-5', className)}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          {icon && (
            <div
              className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl bg-primary-50 text-primary-600"
              aria-hidden
            >
              {icon}
            </div>
          )}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-display text-2xl font-bold tracking-tight text-ink">
                {title}
              </h1>
              {status}
            </div>
            {description && (
              <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-muted">
                {description}
              </p>
            )}
          </div>
        </div>
        {(action || secondaryAction) && (
          <div className="flex flex-shrink-0 flex-wrap items-center gap-2">
            {secondaryAction}
            {action}
          </div>
        )}
      </div>
      {toolbar && (
        <div className="mt-4 border-t border-line pt-4">{toolbar}</div>
      )}
    </header>
  );
}
