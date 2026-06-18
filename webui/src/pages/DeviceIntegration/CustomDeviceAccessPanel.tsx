import { useEffect, useMemo } from 'react';
import { ChevronLeft, MessageSquare, Route, Workflow, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import SessionChat from '@/components/common/SessionChat';
import { useSessionChat } from '@/hooks/useSessionChat';
import type { CustomDeviceAccessMode } from '@/types';
import {
  buildCustomDeviceSessionContext,
  buildCustomDeviceWelcomeMessage,
} from './customDevice';

export default function CustomDeviceAccessPanel({
  mode,
  onClose,
  onBack,
}: {
  mode: CustomDeviceAccessMode;
  onClose: () => void;
  onBack: () => void;
}) {
  const navigate = useNavigate();
  const { t } = useTranslation('device');
  const isWorkflow = mode === 'workflow';
  const title = useMemo(() => t(`custom.title.${mode}`), [mode, t]);
  const subtitle = useMemo(() => t(`custom.subtitle.${mode}`), [mode, t]);
  const welcomeMessage = useMemo(
    () => (isWorkflow ? t(`custom.welcome.${mode}`) : buildCustomDeviceWelcomeMessage(mode)),
    [isWorkflow, mode, t],
  );

  const { sessionId, createAndSend, reset } = useSessionChat({
    title,
    category: 'entity-config',
    contextMessage: buildCustomDeviceSessionContext(mode),
    welcomeMessage,
  });

  useEffect(() => reset, [reset]);

  const handleOpenSession = () => {
    if (!sessionId) return;
    const params = new URLSearchParams({ session: sessionId });
    navigate(`/sessions?${params.toString()}`);
  };

  return (
    <div className="fixed inset-y-0 right-0 flex items-start justify-end z-40 pointer-events-none">
      <div
        className="pointer-events-auto bg-white shadow-2xl border-l border-zinc-200 flex flex-col"
        style={{ width: 'min(560px, 100vw)', height: '100vh' }}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-100 flex-shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <button
              onClick={onBack}
              className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-500 hover:text-zinc-700 transition-colors flex-shrink-0"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${
              isWorkflow ? 'bg-emerald-50' : 'bg-blue-50'
            }`}>
              {mode === 'api' ? <MessageSquare className="w-4 h-4 text-blue-500" /> : null}
              {mode === 'webcli' ? <Route className="w-4 h-4 text-blue-500" /> : null}
              {mode === 'workflow' ? <Workflow className="w-4 h-4 text-emerald-600" /> : null}
            </div>
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-zinc-900 truncate">{title}</h3>
              <p className="text-xs text-zinc-400 mt-0.5">{subtitle}</p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-400 hover:text-zinc-600 flex-shrink-0">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className={isWorkflow ? 'flex-1 min-h-0 overflow-y-auto px-5 py-4' : 'flex-1 min-h-0 overflow-hidden'}>
          {isWorkflow ? (
            <div className="space-y-4">
              <div className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3">
                <p className="text-sm font-medium text-emerald-800">{t('custom.workflow.heading')}</p>
                <p className="text-xs text-emerald-700 mt-1.5 leading-relaxed">
                  {t('custom.workflow.body')}
                </p>
              </div>

              <div className="rounded-xl border border-zinc-100 px-4 py-3 space-y-2">
                <p className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">{t('custom.workflow.requirementsTitle')}</p>
                <ul className="text-sm text-zinc-600 space-y-1.5 list-disc pl-5">
                  <li>{t('custom.workflow.requirement1')}</li>
                  <li>{t('custom.workflow.requirement2')}</li>
                  <li>{t('custom.workflow.requirement3')}</li>
                </ul>
              </div>
            </div>
          ) : (
            <div className="flex h-full min-h-0 flex-col">
              <SessionChat
                sessionId={sessionId}
                live={!!sessionId}
                className="flex-1 min-h-0"
                display={{ compact: true, fullWidth: true }}
                placeholder={mode === 'api' ? t('custom.rex.apiPlaceholder') : t('custom.rex.webcliPlaceholder')}
                emptyText={t('custom.rex.pending')}
                welcomeContent={
                  <div className="mx-4 my-4 flex items-start gap-2.5">
                    <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-red-500 text-sm font-bold text-white shadow-sm ring-2 ring-white">
                      R
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="mb-1.5 text-xs font-semibold text-zinc-700">Rex</div>
                      <div className="rounded-2xl border border-zinc-200 bg-white px-4 py-3 text-sm leading-relaxed text-zinc-700 shadow-sm whitespace-pre-line">
                        {welcomeMessage}
                      </div>
                    </div>
                  </div>
                }
                onCreateAndSend={!sessionId ? (text, imageParts) => createAndSend({ text, imageParts }) : undefined}
              />
            </div>
          )}
        </div>

        <div className="border-t border-zinc-100 px-4 py-2.5 flex-shrink-0">
          {isWorkflow ? (
            <div className="flex items-center justify-between gap-2">
              <button
                onClick={onBack}
                className="px-4 py-2 text-sm rounded-lg border border-zinc-200 text-zinc-600 hover:bg-zinc-50 transition-colors"
              >
                {t('custom.actions.backToSelection')}
              </button>
              <button
                onClick={() => {
                  onClose();
                  navigate('/workflows');
                }}
                className="px-4 py-2 text-sm rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
              >
                {t('custom.workflow.goToWorkflows')}
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-1.5">
              <div className="flex items-center gap-1.5">
                <button
                  onClick={onBack}
                  className="px-3.5 py-2 text-sm rounded-lg border border-zinc-200 text-zinc-600 hover:bg-zinc-50 transition-colors"
                >
                  {t('custom.actions.backToSelection')}
                </button>
                {sessionId && (
                  <button
                    onClick={handleOpenSession}
                    className="inline-flex items-center gap-1.5 px-3.5 py-2 text-sm rounded-lg border border-zinc-200 text-zinc-600 hover:bg-zinc-50 transition-colors"
                  >
                    <MessageSquare className="w-3.5 h-3.5" />
                    {t('custom.actions.openSessionList')}
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
