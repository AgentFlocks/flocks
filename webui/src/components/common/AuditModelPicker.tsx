import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { ChatModelPicker, useChatModelOptions } from './ChatPromptSelectors';

export interface AuditModelPickerProps {
  value: string;
  onChange: (value: string) => void;
  onReady: (ready: boolean) => void;
}

export default function AuditModelPicker({ value, onChange, onReady }: AuditModelPickerProps) {
  const { t } = useTranslation('session');
  const { groupedOptions, options, primaryModelOption, resolvedDefaultModelInitialized, loading } = useChatModelOptions({ enableAuto: false });
  const selected = options.find(option => `${option.providerID}/${option.modelID}` === value) ?? null;
  const initial = primaryModelOption ?? options[0] ?? null;
  useEffect(() => {
    if (!loading && resolvedDefaultModelInitialized && !value && initial) onChange(`${initial.providerID}/${initial.modelID}`);
  }, [loading, resolvedDefaultModelInitialized, value, initial, onChange]);
  useEffect(() => {
    onReady(!loading && selected !== null);
  }, [loading, selected, onReady]);
  useEffect(() => () => onReady(false), [onReady]);
  return (
    <div>
      <ChatModelPicker groupedOptions={groupedOptions} loading={loading}
        selectedModelOption={selected}
        onSelectModel={option => onChange(`${option.providerID}/${option.modelID}`)} />
      {!loading && !options.length && <p role="status" className="mt-2 text-xs text-zinc-500">{t('modelPicker.empty')}</p>}
    </div>
  );
}
