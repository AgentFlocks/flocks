import { useId, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { IconX, type TablerIcon } from '@tabler/icons-react';

interface NoticeCardProps {
  title: ReactNode;
  subtitle?: ReactNode;
  icon: TablerIcon;
  closeLabel: string;
  onClose: () => void;
  children: ReactNode;
}

/** Shared appearance for automatic reminders; upgrade progress stays separate. */
export default function NoticeCard({
  title,
  subtitle,
  icon: Icon,
  closeLabel,
  onClose,
  children,
}: NoticeCardProps) {
  const titleId = useId();
  return (
    <div className="fixed inset-x-4 bottom-4 z-[100] pointer-events-none sm:inset-x-auto sm:left-4 sm:w-full sm:max-w-sm lg:left-6 lg:bottom-6">
      <section
        role="dialog"
        aria-modal="false"
        aria-labelledby={titleId}
        className="pointer-events-auto rounded-card border border-warning bg-surface shadow-2xl overflow-hidden"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line bg-warning-soft px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-control bg-warning-soft text-warning">
              <Icon aria-hidden="true" className="h-4 w-4" />
            </span>
            <div>
              <div id={titleId} className="text-sm font-semibold text-warning">
                {title}
              </div>
              {subtitle && <div className="text-xs text-warning">{subtitle}</div>}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label={closeLabel}
            className="ui-btn ui-btn-ghost ui-btn-icon text-warning"
          >
            <IconX aria-hidden="true" className="w-4 h-4" />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}

export function NoticePrimaryAction({ className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`ui-btn ui-btn-primary ${className}`}
    />
  );
}
