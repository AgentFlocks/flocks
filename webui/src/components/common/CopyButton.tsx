import { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useToast } from './Toast';
import { copyText } from '@/utils/clipboard';

interface CopyButtonProps {
  text: string;
  /** Icon size class, e.g. "w-3 h-3" or "w-3.5 h-3.5". Defaults to "w-3.5 h-3.5". */
  size?: string;
  label?: string;
  /** Classes for the visible label, e.g. to hide it on phones while it stays the accessible name. */
  labelClassName?: string;
  className?: string;
}

export default function CopyButton({
  text,
  size = 'w-3.5 h-3.5',
  label,
  labelClassName,
  className,
}: CopyButtonProps) {
  const { t } = useTranslation('common');
  const [copied, setCopied] = useState(false);
  const toast = useToast();

  const handleCopy = async () => {
    try {
      await copyText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      toast.error(
        t('clipboard.copyFailedTitle'),
        error instanceof Error ? error.message : t('clipboard.copyFailedDescription'),
      );
    }
  };

  return (
    <button
      onClick={handleCopy}
      aria-label={label ?? t('button.copy')}
      className={className ?? 'p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0'}
      title={label ?? t('button.copy')}
    >
      <span className="inline-flex items-center gap-1.5">
        {copied
          ? <Check className={`${size} text-green-500`} />
          : <Copy className={size} />}
        {label && <span className={labelClassName}>{label}</span>}
      </span>
    </button>
  );
}
