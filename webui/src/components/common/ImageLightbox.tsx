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

import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

interface ImageLightboxProps {
  /** Image source URL — supports both `data:` URLs and remote http(s) URLs. */
  src: string;
  /** Optional filename / alt text shown for screen readers. */
  alt?: string;
  /** Called when the user dismisses the lightbox (Escape, click outside, ✕). */
  onClose: () => void;
}

// Module-level reference count + saved baseline so two concurrently-mounted
// lightboxes don't fight over ``body.style.overflow``. Without this, the
// first lightbox to unmount would prematurely restore the baseline while
// the second is still open, and the second's restore would then "lock in"
// the previous (already-hidden) value.
let scrollLockCount = 0;
let savedBodyOverflow = '';

function acquireScrollLock(): void {
  if (scrollLockCount === 0) {
    savedBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  }
  scrollLockCount += 1;
}

function releaseScrollLock(): void {
  scrollLockCount = Math.max(0, scrollLockCount - 1);
  if (scrollLockCount === 0) {
    document.body.style.overflow = savedBodyOverflow;
  }
}

export default function ImageLightbox({ src, alt, onClose }: ImageLightboxProps) {
  const { t } = useTranslation('common');
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  // Close on Escape so the overlay behaves like a normal modal.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // Lock the body scroll while the lightbox is mounted.
  useEffect(() => {
    acquireScrollLock();
    return releaseScrollLock;
  }, []);

  // Move focus to the close button so keyboard users can dismiss immediately.
  useEffect(() => {
    closeButtonRef.current?.focus();
  }, []);

  const label = alt || t('image.preview');

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm p-6 cursor-zoom-out"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={label}
    >
      <button
        ref={closeButtonRef}
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        className="absolute top-4 right-4 inline-flex items-center justify-center w-10 h-10 rounded-full bg-white/10 text-white hover:bg-white/20 transition-colors"
        aria-label={t('button.close')}
      >
        <X className="w-5 h-5" />
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
