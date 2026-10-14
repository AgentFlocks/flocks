export interface PillOption<T extends string> {
  value: T;
  label: string;
  /** Tailwind classes applied when this option is selected */
  activeClass: string;
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
    <div className="flex flex-wrap gap-2">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          disabled={disabled}
          onClick={() => onChange(opt.value)}
          className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors ${
            value === opt.value
              ? opt.activeClass
              : 'bg-white text-gray-500 border-gray-300 hover:border-gray-400 hover:text-gray-700'
          } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
