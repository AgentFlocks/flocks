interface PageHeaderProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
}

export default function PageHeader({ title, description, action, icon }: PageHeaderProps) {
  return (
    <div className="mb-6 max-md:mb-4">
      {/* Phones: the actions wrap under the title instead of squeezing it into a column. */}
      <div className="flex items-center justify-between max-md:flex-wrap max-md:gap-3">
        <div className="flex items-center gap-3 max-md:min-w-0">
          {icon && <div className="text-red-600 max-md:shrink-0">{icon}</div>}
          <div className="max-md:min-w-0">
            <h1 className="text-2xl font-bold text-gray-900 max-md:text-xl">{title}</h1>
            {description && (
              <p className="mt-1 text-sm text-gray-500">{description}</p>
            )}
          </div>
        </div>
        {action && <div className="max-md:max-w-full max-md:[&>.flex]:flex-wrap">{action}</div>}
      </div>
    </div>
  );
}
