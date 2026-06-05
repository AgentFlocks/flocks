import { useEffect, useMemo, useState, type InputHTMLAttributes, type TextareaHTMLAttributes } from 'react';
import { ChevronLeft, Loader2, MessageSquare, Route, Workflow, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useToast } from '@/components/common/Toast';
import SessionChat from '@/components/common/SessionChat';
import { useSessionChat } from '@/hooks/useSessionChat';
import type {
  CustomDeviceAccessMode,
  CustomDeviceApiDraft,
  CustomDeviceWebCliDraft,
} from '@/types';
import {
  buildCustomDevicePrompt,
  buildCustomDeviceSessionContext,
} from './customDevice';

type PanelView = 'details' | 'rex' | 'guide';

const EMPTY_API_DRAFT: CustomDeviceApiDraft = {
  accessMode: 'api',
  deviceName: '',
  vendorName: '',
  version: '',
  baseUrl: '',
  docsUrl: '',
  capabilities: '',
};

const EMPTY_WEBCLI_DRAFT: CustomDeviceWebCliDraft = {
  accessMode: 'webcli',
  deviceName: '',
  vendorName: '',
  version: '',
  productUrl: '',
  targetInterfaces: '',
  authHint: '',
};

function FieldLabel({ label, required = false }: { label: string; required?: boolean }) {
  return (
    <label className="block text-xs font-medium text-zinc-600 mb-1.5">
      {label}
      {required && <span className="text-red-500 ml-0.5">*</span>}
    </label>
  );
}

function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100 ${
        props.className ?? ''
      }`}
    />
  );
}

function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100 ${
        props.className ?? ''
      }`}
    />
  );
}

function validateDraft(
  draft: CustomDeviceApiDraft | CustomDeviceWebCliDraft,
  t: (key: string) => string,
): string | null {
  if (!draft.deviceName.trim()) return t('custom.validation.deviceNameRequired');
  if (!draft.vendorName.trim()) return t('custom.validation.vendorNameRequired');

  if (draft.accessMode === 'api') {
    if (!draft.baseUrl.trim()) return t('custom.validation.baseUrlRequired');
    return null;
  }

  if (!draft.productUrl.trim()) return t('custom.validation.productUrlRequired');
  if (!draft.targetInterfaces.trim()) return t('custom.validation.targetInterfacesRequired');
  return null;
}

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
  const toast = useToast();
  const { t } = useTranslation('device');
  const isWorkflow = mode === 'workflow';
  const [view, setView] = useState<PanelView>(isWorkflow ? 'guide' : 'details');
  const [apiDraft, setApiDraft] = useState<CustomDeviceApiDraft>(EMPTY_API_DRAFT);
  const [webcliDraft, setWebcliDraft] = useState<CustomDeviceWebCliDraft>(EMPTY_WEBCLI_DRAFT);
  const [submitting, setSubmitting] = useState(false);

  const draft = mode === 'api' ? apiDraft : mode === 'webcli' ? webcliDraft : null;
  const isRexView = !isWorkflow && view === 'rex';
  const title = useMemo(() => t(`custom.title.${mode}`), [mode, t]);
  const subtitle = useMemo(() => t(`custom.subtitle.${mode}`), [mode, t]);
  const welcomeMessage = useMemo(() => t(`custom.welcome.${mode}`), [mode, t]);

  const { sessionId, createAndSend, reset } = useSessionChat({
    title: draft?.deviceName.trim() ? `${title}：${draft.deviceName.trim()}` : title,
    category: 'entity-config',
    contextMessage: buildCustomDeviceSessionContext(mode),
    welcomeMessage,
  });

  useEffect(() => reset, [reset]);

  const handleSubmitToRex = async () => {
    if (!draft) return;
    const error = validateDraft(draft, t);
    if (error) {
      toast.error(error);
      return;
    }
    setSubmitting(true);
    try {
      await createAndSend({ text: buildCustomDevicePrompt(draft) });
      setView('rex');
      toast.success(t('custom.toast.submitSuccess'));
    } catch {
      toast.error(t('custom.toast.submitFailed'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleOpenSession = () => {
    if (!sessionId) return;
    const params = new URLSearchParams({ session: sessionId });
    navigate(`/sessions?${params.toString()}`);
  };

  const renderApiForm = () => (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <FieldLabel label={t('custom.form.api.deviceNameLabel')} required />
          <TextInput
            aria-label={t('custom.form.api.deviceNameLabel')}
            value={apiDraft.deviceName}
            onChange={(e) => setApiDraft((prev) => ({ ...prev, deviceName: e.target.value }))}
            placeholder={t('custom.form.api.deviceNamePlaceholder')}
          />
        </div>
        <div>
          <FieldLabel label={t('custom.form.api.vendorNameLabel')} required />
          <TextInput
            aria-label={t('custom.form.api.vendorNameLabel')}
            value={apiDraft.vendorName}
            onChange={(e) => setApiDraft((prev) => ({ ...prev, vendorName: e.target.value }))}
            placeholder={t('custom.form.api.vendorNamePlaceholder')}
          />
        </div>
      </div>

      <div>
        <FieldLabel label={t('custom.form.common.versionLabel')} />
        <TextInput
          aria-label={t('custom.form.common.versionLabel')}
          value={apiDraft.version}
          onChange={(e) => setApiDraft((prev) => ({ ...prev, version: e.target.value }))}
          placeholder={t('custom.form.api.versionPlaceholder')}
        />
      </div>

      <div>
        <FieldLabel label={t('custom.form.api.baseUrlLabel')} required />
        <TextInput
          aria-label={t('custom.form.api.baseUrlLabel')}
          value={apiDraft.baseUrl}
          onChange={(e) => setApiDraft((prev) => ({ ...prev, baseUrl: e.target.value }))}
          placeholder={t('custom.form.api.baseUrlPlaceholder')}
        />
      </div>

      <div>
        <FieldLabel label={t('custom.form.api.docsUrlLabel')} />
        <TextInput
          aria-label={t('custom.form.api.docsUrlLabel')}
          value={apiDraft.docsUrl}
          onChange={(e) => setApiDraft((prev) => ({ ...prev, docsUrl: e.target.value }))}
          placeholder={t('custom.form.api.docsUrlPlaceholder')}
        />
        <p className="mt-1 text-[11px] text-zinc-400">{t('custom.form.api.docsUrlHint')}</p>
      </div>

      <div>
        <FieldLabel label={t('custom.form.api.capabilitiesLabel')} />
        <TextArea
          aria-label={t('custom.form.api.capabilitiesLabel')}
          value={apiDraft.capabilities}
          onChange={(e) => setApiDraft((prev) => ({ ...prev, capabilities: e.target.value }))}
          placeholder={t('custom.form.api.capabilitiesPlaceholder')}
          rows={3}
        />
        <p className="mt-1 text-[11px] text-zinc-400">{t('custom.form.api.capabilitiesHint')}</p>
      </div>
    </div>
  );

  const renderWebCliForm = () => (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <FieldLabel label={t('custom.form.webcli.deviceNameLabel')} required />
          <TextInput
            aria-label={t('custom.form.webcli.deviceNameLabel')}
            value={webcliDraft.deviceName}
            onChange={(e) => setWebcliDraft((prev) => ({ ...prev, deviceName: e.target.value }))}
            placeholder={t('custom.form.webcli.deviceNamePlaceholder')}
          />
        </div>
        <div>
          <FieldLabel label={t('custom.form.webcli.vendorNameLabel')} required />
          <TextInput
            aria-label={t('custom.form.webcli.vendorNameLabel')}
            value={webcliDraft.vendorName}
            onChange={(e) => setWebcliDraft((prev) => ({ ...prev, vendorName: e.target.value }))}
            placeholder={t('custom.form.webcli.vendorNamePlaceholder')}
          />
        </div>
      </div>

      <div>
        <FieldLabel label={t('custom.form.common.versionLabel')} />
        <TextInput
          aria-label={t('custom.form.common.versionLabel')}
          value={webcliDraft.version}
          onChange={(e) => setWebcliDraft((prev) => ({ ...prev, version: e.target.value }))}
          placeholder={t('custom.form.webcli.versionPlaceholder')}
        />
      </div>

      <div>
        <FieldLabel label={t('custom.form.webcli.productUrlLabel')} required />
        <TextInput
          aria-label={t('custom.form.webcli.productUrlLabel')}
          value={webcliDraft.productUrl}
          onChange={(e) => setWebcliDraft((prev) => ({ ...prev, productUrl: e.target.value }))}
          placeholder={t('custom.form.webcli.productUrlPlaceholder')}
        />
      </div>

      <div>
        <FieldLabel label={t('custom.form.webcli.targetInterfacesLabel')} required />
        <TextArea
          aria-label={t('custom.form.webcli.targetInterfacesLabel')}
          value={webcliDraft.targetInterfaces}
          onChange={(e) => setWebcliDraft((prev) => ({ ...prev, targetInterfaces: e.target.value }))}
          placeholder={t('custom.form.webcli.targetInterfacesPlaceholder')}
          rows={5}
        />
      </div>

      <div>
        <FieldLabel label={t('custom.form.webcli.authHintLabel')} />
        <TextArea
          aria-label={t('custom.form.webcli.authHintLabel')}
          value={webcliDraft.authHint}
          onChange={(e) => setWebcliDraft((prev) => ({ ...prev, authHint: e.target.value }))}
          placeholder={t('custom.form.webcli.authHintPlaceholder')}
          rows={3}
        />
      </div>
    </div>
  );

  return (
    <div className="fixed inset-y-0 right-0 flex items-start justify-end z-40 pointer-events-none">
      <div
        className="pointer-events-auto bg-white shadow-2xl border-l border-zinc-200 flex flex-col"
        style={{ width: 520, marginTop: 64, height: 'calc(100vh - 64px)' }}
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

        <div className={isRexView ? 'flex-1 min-h-0 overflow-hidden' : 'flex-1 min-h-0 overflow-y-auto px-5 py-4'}>
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
          ) : view === 'details' ? (
            <div className="space-y-4">
              <div className="rounded-xl border border-zinc-100 bg-zinc-50 px-4 py-3">
                <p className="text-sm font-medium text-zinc-800">{t('custom.details.prepareTitle')}</p>
                <p className="text-xs text-zinc-500 mt-1.5 leading-relaxed">
                  {t('custom.details.prepareIntro')}
                  {mode === 'api'
                    ? t('custom.details.apiNext')
                    : t('custom.details.webcliNext')}
                </p>
              </div>
              {mode === 'api' ? renderApiForm() : renderWebCliForm()}
            </div>
          ) : (
            <div className="flex h-full min-h-0 flex-col">
              <div className="flex-shrink-0 px-5 py-3 border-b border-zinc-100 bg-zinc-50">
                <p className="text-xs text-zinc-500 leading-relaxed">
                  {mode === 'api'
                    ? t('custom.rex.apiHint')
                    : t('custom.rex.webcliHint')}
                </p>
              </div>
              <SessionChat
                sessionId={sessionId}
                live={!!sessionId}
                className="flex-1 min-h-0"
                placeholder={t('custom.rex.placeholder')}
                emptyText={t('custom.rex.pending')}
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
                  onClick={view === 'details' ? onBack : () => setView('details')}
                  className="px-3.5 py-2 text-sm rounded-lg border border-zinc-200 text-zinc-600 hover:bg-zinc-50 transition-colors"
                >
                  {view === 'details' ? t('custom.actions.backToSelection') : t('custom.actions.backToForm')}
                </button>
                {view === 'rex' && sessionId && (
                  <button
                    onClick={handleOpenSession}
                    className="inline-flex items-center gap-1.5 px-3.5 py-2 text-sm rounded-lg border border-zinc-200 text-zinc-600 hover:bg-zinc-50 transition-colors"
                  >
                    <MessageSquare className="w-3.5 h-3.5" />
                    {t('custom.actions.openSessionList')}
                  </button>
                )}
              </div>
              {view === 'details' && (
                <button
                  onClick={() => void handleSubmitToRex()}
                  disabled={submitting}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
                >
                  {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <MessageSquare className="w-3.5 h-3.5" />}
                  {t('custom.actions.submit')}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
