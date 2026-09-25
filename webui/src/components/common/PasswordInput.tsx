import { useState, forwardRef } from 'react';
import type { InputHTMLAttributes } from 'react';
import { IconEye, IconEyeOff } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';

type PasswordInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>;

const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ className = '', ...rest }, ref) => {
    const [visible, setVisible] = useState(false);
    const { t } = useTranslation('auth');

    const baseClass =
      'w-full border border-line-control rounded-lg px-3 py-2 pr-10 outline-none focus:border-info';

    return (
      <div className="relative">
        <input
          {...rest}
          ref={ref}
          type={visible ? 'text' : 'password'}
          className={className ? `${baseClass} ${className}` : baseClass}
        />
        <button
          type="button"
          tabIndex={-1}
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? t('fields.hidePassword') : t('fields.showPassword')}
          className="absolute inset-y-0 right-0 flex items-center px-3 text-fg-muted hover:text-fg-secondary focus:outline-none"
        >
          {visible ? <IconEyeOff aria-hidden="true" className="h-4 w-4" /> : <IconEye aria-hidden="true" className="h-4 w-4" />}
        </button>
      </div>
    );
  },
);

PasswordInput.displayName = 'PasswordInput';

export default PasswordInput;
