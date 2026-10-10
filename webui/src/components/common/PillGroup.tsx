export interface PillOption<T extends string> {
  value: T;
  label: string;
  /** Tailwind classes applied when this option is selected */
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
    <div className="flex flex-wrap gap-2" role="group">
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
          className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors ${
            value === opt.value
              ? opt.activeClass ?? 'bg-slate-600 text-white border-slate-600'
              : 'bg-white text-gray-500 border-gray-300 hover:border-gray-400 hover:text-gray-700'
          } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
