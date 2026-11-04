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
        <div className="w-16 h-16 bg-ui-subtle rounded-full flex items-center justify-center mb-4 text-ui-secondary [&_svg]:stroke-[1.75]">
          {icon}
        </div>
      )}
      <h3 className="text-lg font-semibold text-ui-text mb-2">{title}</h3>
      {description && (
        <p className="text-sm leading-relaxed text-ui-secondary text-center max-w-md mb-4">{description}</p>
      )}
      {action}
    </div>
  );
}
