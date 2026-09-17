import { useId, useLayoutEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export default function PrototypeDialog({
  title,
  children,
  onClose,
  drawer = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  drawer?: boolean;
}) {
  const { t } = useTranslation('pluginLibrary');
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useLayoutEffect(() => {
    const dialog = ref.current;
    const previousFocus = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected)
        previousFocus.focus();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        )
          onClose();
      }}
      className={`bg-white p-0 text-gray-900 shadow-2xl backdrop:bg-slate-950/40 ${
        drawer
          ? 'fixed inset-y-0 left-auto right-0 m-0 h-dvh max-h-none w-full max-w-lg border-0 border-l border-gray-200'
          : 'w-[calc(100%_-_2rem)] max-w-md rounded-2xl border border-gray-200'
      }`}
    >
      <div
        className="flex max-h-[90dvh] flex-col"
        style={drawer ? { maxHeight: '100dvh', height: '100%' } : undefined}
      >
        <div className="flex items-center justify-between gap-4 border-b border-gray-100 px-6 py-5">
          <h2 id={titleId} className="text-base font-semibold">
            {title}
          </h2>
          <button
            type="button"
            autoFocus
            onClick={onClose}
            aria-label={t('close')}
            className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-500"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="overflow-y-auto p-6">{children}</div>
      </div>
    </dialog>
  );
}
