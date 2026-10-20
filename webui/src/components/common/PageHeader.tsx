interface PageHeaderProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
}

export default function PageHeader({ title, description, action, icon }: PageHeaderProps) {
  return (
    <div className="mb-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {icon && <div className="text-red-600 [&_svg]:stroke-[1.75]">{icon}</div>}
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-ui-text">{title}</h1>
            {description && (
              <p className="mt-1 text-sm leading-relaxed text-ui-secondary">{description}</p>
            )}
          </div>
        </div>
        {action && <div>{action}</div>}
      </div>
    </div>
  );
}
