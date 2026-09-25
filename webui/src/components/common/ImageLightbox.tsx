/**
 * ImageLightbox — full-screen overlay for previewing chat images.
 *
 * Why a custom lightbox instead of `window.open(url, '_blank')`:
 * the image URLs flowing through the chat composer are inline base64
 * `data:` URLs. Modern browsers (Chrome / Edge / Firefox) block top-level
 * navigation to `data:` URLs for phishing-protection reasons, so opening
 * one in a new tab silently produces a blank page. Rendering the image
 * inside a same-origin overlay sidesteps the restriction and matches the
 * mental model the user expects ("click to enlarge in place").
 */

import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { IconX } from '@tabler/icons-react';
import { useDialogFocus } from '@/hooks/useDialogFocus';

interface ImageLightboxProps {
  /** Image source URL — supports both `data:` URLs and remote http(s) URLs. */
  src: string;
  /** Optional filename / alt text shown for screen readers. */
  alt?: string;
  /** Called when the user dismisses the lightbox (Escape, click outside, ✕). */
  onClose: () => void;
}

export default function ImageLightbox({ src, alt, onClose }: ImageLightboxProps) {
  const { t } = useTranslation('common');
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, true, onClose);

  const label = alt || t('image.preview');

  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      data-ui-overlay="dialog"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-overlay-backdrop backdrop-blur-sm p-6 cursor-zoom-out"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={label}
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        className="ui-btn ui-btn-icon absolute top-4 right-4 bg-surface text-fg"
        aria-label={t('button.close')}
      >
        <IconX aria-hidden="true" className="w-5 h-5" />
      </button>
      <img
        src={src}
        alt={alt || ''}
        className="max-w-full max-h-full object-contain rounded-lg shadow-2xl cursor-default"
        onClick={(e) => e.stopPropagation()}
      />
    </div>
  );
}
