import { useId, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { X, type LucideIcon } from 'lucide-react';

interface NoticeCardProps {
  title: ReactNode;
  subtitle?: ReactNode;
  icon: LucideIcon;
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
        className="pointer-events-auto rounded-2xl border border-amber-200 bg-white shadow-2xl overflow-hidden"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-amber-100 bg-gradient-to-r from-amber-50 via-orange-50 to-rose-50 px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-amber-500 text-white shadow-sm">
              <Icon aria-hidden="true" className="h-4 w-4" />
            </span>
            <div>
              <div id={titleId} className="text-sm font-semibold text-amber-950">
                {title}
              </div>
              {subtitle && <div className="text-xs text-amber-700">{subtitle}</div>}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label={closeLabel}
            className="p-1 text-gray-400 hover:text-gray-600 rounded transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600"
          >
            <X aria-hidden="true" className="w-4 h-4" />
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
      className={`flex items-center gap-1.5 rounded-lg bg-amber-500 px-4 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-amber-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600 ${className}`}
    />
  );
}
