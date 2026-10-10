interface PageHeaderProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
  tone?: 'ai' | 'data' | 'brand';
}

export default function PageHeader({ title, description, action, icon }: PageHeaderProps) {
  // dev colours every page icon red; `tone` is still accepted so callers need not change.
  const iconClass = 'text-red-600';
  return (
    <div className="mb-6">
      {/* flex-wrap: on phone widths the action drops below the title instead
          of being squeezed off the right edge of the viewport. */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
        <div className="flex items-center gap-3 min-w-0">
          {icon && <div className={`flex-shrink-0 ${iconClass}`}>{icon}</div>}
          <div className="min-w-0">
            <h1 className="text-2xl font-bold text-gray-900">{title}</h1>
            {description && (
              <p className="mt-1 text-sm text-gray-500">{description}</p>
            )}
          </div>
        </div>
        {action && <div className="ml-auto min-w-0 max-w-full flex-shrink-0">{action}</div>}
      </div>
    </div>
  );
}
