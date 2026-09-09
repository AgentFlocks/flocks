import { createPortal } from 'react-dom';
import { useEffect } from 'react';
import { Bell } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import NoticeCard, { NoticePrimaryAction } from './NoticeCard';

export default function TokenPolicyNotice({
  onClose,
  onPresented,
}: {
  onClose: () => void;
  onPresented: () => void;
}) {
  const { t } = useTranslation('notification');
  useEffect(() => {
    // Confirm only after a foreground paint, not when the module is downloaded.
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        if (document.visibilityState === 'visible') onPresented();
      });
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onPresented();
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose, onPresented]);
  const close = () => {
    onPresented();
    onClose();
  };
  return createPortal(
    <NoticeCard title={t('tokenPolicyTitle')} icon={Bell} closeLabel={t('close')} onClose={close}>
      <div className="px-4 py-4">
        <p
          role="status"
          className="rounded-xl border border-amber-100 bg-amber-50/70 px-3 py-2 text-sm leading-6 text-amber-900"
        >
          {t('tokenPolicyBody')}
        </p>
      </div>
      <div className="px-4 pb-4 flex items-center justify-end">
        <NoticePrimaryAction onClick={close}>{t('gotIt')}</NoticePrimaryAction>
      </div>
    </NoticeCard>,
    document.body,
  );
}
