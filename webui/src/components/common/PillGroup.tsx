export interface PillOption<T extends string> {
  value: T;
  label: string;
  /** @deprecated Selection uses the shared semantic style in both themes. */
  activeClass?: string;
}

export default function PillGroup<T extends string>({
  options,
  value,
  onChange,
  disabled,
}: {
  options: PillOption<T>[];
  value: T;
  onChange: (v: T) => void;
  disabled?: boolean;
}) {
  return (
    <div className="ui-segmented flex flex-wrap gap-1" role="group">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          disabled={disabled}
          aria-pressed={value === opt.value}
          tabIndex={value === opt.value || !options.some(option => option.value === value) ? 0 : -1}
          onClick={() => onChange(opt.value)}
          onKeyDown={(event) => {
            const index = options.findIndex(option => option.value === opt.value);
            const next = event.key === 'ArrowRight' || event.key === 'ArrowDown'
              ? (index + 1) % options.length
              : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
                ? (index - 1 + options.length) % options.length
                : event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : null;
            if (next === null || disabled) return;
            event.preventDefault();
            onChange(options[next].value);
            event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('button')[next]?.focus();
          }}
          className={`px-3 py-1.5 rounded-segment text-sm transition-colors ${
            value === opt.value
              ? 'bg-selected text-fg font-semibold'
              : 'bg-transparent text-fg-secondary hover:bg-hover'
          } ${disabled ? 'bg-disabled text-fg-disabled cursor-not-allowed' : 'cursor-pointer'}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
