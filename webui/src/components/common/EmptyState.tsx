interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export default function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-4">
      {icon && (
        <div className="w-16 h-16 bg-subtle rounded-full flex items-center justify-center mb-4 text-fg-muted">
          {icon}
        </div>
      )}
      <h3 className="text-title-card font-semibold text-fg mb-2">{title}</h3>
      {description && (
        <p className="text-sm text-fg-muted text-center max-w-md mb-4">{description}</p>
      )}
      {action}
    </div>
  );
}
