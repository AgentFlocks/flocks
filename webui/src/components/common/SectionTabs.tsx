import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';

export interface SectionTabItem<T extends string> {
  key: T;
  label: ReactNode;
  icon?: ReactNode;
  count?: number;
}

export interface SectionTabsProps<T extends string> {
  items: SectionTabItem<T>[];
  activeKey: T;
  onChange: (key: T) => void;
  className?: string;
  'aria-label'?: string;
}

export default function SectionTabs<T extends string>({
  items,
  activeKey,
  onChange,
  className,
  'aria-label': ariaLabel,
}: SectionTabsProps<T>) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn('flocks-segmented', className)}
    >
      {items.map((item, idx) => {
        const active = item.key === activeKey;
        return (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(item.key)}
            className={cn(
              'flocks-segmented-item inline-flex items-center gap-2',
              active && 'flocks-segmented-item-active',
              idx > 0 && 'ml-0.5',
            )}
          >
            {item.icon}
            <span>{item.label}</span>
            {item.count != null && item.count > 0 && (
              <span
                className={cn(
                  'min-w-[1.25rem] rounded px-1 text-[10px] tabular-nums',
                  active ? 'bg-gray-100 text-gray-600' : 'bg-gray-200/80 text-gray-500',
                )}
              >
                {item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
