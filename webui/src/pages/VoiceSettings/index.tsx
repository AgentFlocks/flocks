import { useEffect, useState } from 'react';
import { CheckCircle, Loader2, Mic, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import { voiceApi, VOICE_STATUS_CHANGED, type VoiceCaptureMode, type VoiceRecognitionScene, type VoiceSettings, type VoiceSettingsUpdate } from '@/api/voice';

export default function VoiceSettingsPage() {
  const { t } = useTranslation('voice');
  const [settings, setSettings] = useState<VoiceSettings | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [captureMode, setCaptureMode] = useState<VoiceCaptureMode>('voice');
  const [recognitionScene, setRecognitionScene] = useState<VoiceRecognitionScene>('general');
  const [hotwords, setHotwords] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let mounted = true;
    voiceApi.settings().then((value) => {
      if (!mounted) return;
      setSettings(value);
      setCaptureMode(value.capture_mode ?? 'voice');
      setRecognitionScene(value.recognition_scene ?? 'general');
      setHotwords((value.hotwords ?? []).join('\n'));
    })
      .catch(() => { if (mounted) setError(t('settings.loadError')); })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [t]);

  const normalizedHotwords = [...new Set(hotwords.split(/[\n,，]/).map((word) => word.trim()).filter(Boolean))];
  const hotwordError = normalizedHotwords.length > 100 ? t('settings.hotwordsTooMany')
    : normalizedHotwords.some((word) => Array.from(word).length > 64) ? t('settings.hotwordTooLong') : '';

  const update = async (payload: VoiceSettingsUpdate) => {
    if (saving) return;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const value = await voiceApi.updateSettings(payload);
      setSettings(value);
      if ('api_key' in payload) setApiKey('');
      if ('hotwords' in payload) setHotwords(value.hotwords.join('\n'));
      if ('capture_mode' in payload) setCaptureMode(value.capture_mode);
      if ('recognition_scene' in payload) setRecognitionScene(value.recognition_scene ?? 'general');
      setNotice(t('settings.saved'));
      window.dispatchEvent(new Event(VOICE_STATUS_CHANGED));
    } catch {
      setError(t('settings.saveError'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader title={t('settings.title')} description={t('settings.description')} />
      {loading && <div role="status" className="flex items-center gap-2 text-sm text-fg-secondary"><Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />{t('settings.loading')}</div>}
      {error && <p role="alert" className="flex items-start gap-2 rounded-control bg-danger-soft px-3 py-2 text-sm text-danger"><XCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />{error}</p>}
      {settings && (
        <section className="ui-card space-y-6">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h2 className="flex items-center gap-2 text-base font-semibold text-fg"><Mic aria-hidden="true" className="h-4 w-4 shrink-0" />{t('settings.enable')}</h2>
              <p id="voice-enable-help" className="mt-2 text-sm text-fg-secondary">{t('settings.enableHelp')}</p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {/* The state is also written out, not only shown by colour; the label widens the hit area. */}
              <label htmlFor="voice-enable-switch" aria-hidden="true" className="cursor-pointer text-sm text-fg-secondary">
                {t(settings.enabled ? 'settings.stateOn' : 'settings.stateOff')}
              </label>
              <button
                id="voice-enable-switch"
                type="button"
                role="switch"
                aria-label={t('settings.enable')}
                aria-describedby="voice-enable-help"
                aria-checked={settings.enabled}
                disabled={saving || !settings.configured}
                onClick={() => { void update({ enabled: !settings.enabled }); }}
                className="ui-btn-icon inline-flex shrink-0 items-center justify-center rounded-control disabled:cursor-not-allowed">
<span className="ui-switch" aria-hidden="true" aria-checked={settings.enabled} aria-disabled={saving || !settings.configured} />
</button>
            </div>
          </div>
          <form onSubmit={(event) => { event.preventDefault(); if (apiKey.trim()) void update({ api_key: apiKey.trim() }); }} className="space-y-3 border-t border-line pt-5">
            <label htmlFor="doubao-asr-key" className="block text-sm font-medium text-fg">{t('settings.key')}</label>
            <p id="doubao-asr-key-help" className="text-sm text-fg-secondary">{t('settings.keyHelp')}</p>
            <input
              id="doubao-asr-key"
              name="doubao-asr-api-key"
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              value={apiKey}
              maxLength={4096}
              disabled={saving}
              aria-describedby="doubao-asr-key-help"
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={t(settings.configured ? 'settings.replaceKey' : 'settings.enterKey')}
              className="ui-input"
            />
            <p className="text-xs text-fg-secondary">{t(`settings.source.${settings.credential_source}`)}</p>
            <div className="flex flex-wrap gap-3">
              <button type="submit" disabled={saving || !apiKey.trim()} className="ui-btn ui-btn-primary">{t('settings.saveKey')}</button>
              {settings.credential_source === 'stored' && <button type="button" disabled={saving} onClick={() => { void update({ api_key: '' }); }} className="ui-btn">{t('settings.clearKey')}</button>}
            </div>
          </form>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!hotwordError) void update({ recognition_scene: recognitionScene, capture_mode: captureMode, hotwords: normalizedHotwords });
            }}
            className="space-y-4 border-t border-line pt-5"
          >
            <div className="space-y-2">
              <label htmlFor="voice-recognition-scene" className="block text-sm font-medium text-fg">{t('settings.recognitionScene')}</label>
              <select
                id="voice-recognition-scene"
                value={recognitionScene}
                disabled={saving}
                aria-describedby="voice-recognition-scene-help voice-recognition-scene-detail"
                onChange={(event) => setRecognitionScene(event.target.value as VoiceRecognitionScene)}
                className="ui-select"
              >
                <option value="general">{t('settings.sceneGeneral')}</option>
                <option value="security_internet">{t('settings.sceneSecurityInternet')}</option>
              </select>
              <p id="voice-recognition-scene-help" className="text-sm text-fg-secondary">{t('settings.recognitionSceneHelp')}</p>
              <p id="voice-recognition-scene-detail" className="text-sm text-fg-secondary">
                {t(recognitionScene === 'security_internet' ? 'settings.sceneSecurityInternetHelp' : 'settings.sceneGeneralHelp')}
              </p>
            </div>
            <div className="space-y-2">
              <label htmlFor="voice-capture-mode" className="block text-sm font-medium text-fg">{t('settings.captureMode')}</label>
              <select
                id="voice-capture-mode"
                value={captureMode}
                disabled={saving}
                aria-describedby="voice-capture-mode-help"
                onChange={(event) => setCaptureMode(event.target.value as VoiceCaptureMode)}
                className="ui-select"
              >
                <option value="voice">{t('settings.captureVoice')}</option>
                <option value="raw">{t('settings.captureRaw')}</option>
              </select>
              <p id="voice-capture-mode-help" className="text-sm text-fg-secondary">{t('settings.captureModeHelp')}</p>
            </div>
            <div className="space-y-2">
              <label htmlFor="voice-hotwords" className="block text-sm font-medium text-fg">{t('settings.hotwords')}</label>
              <textarea
                id="voice-hotwords"
                value={hotwords}
                rows={4}
                maxLength={10000}
                disabled={saving}
                spellCheck={false}
                aria-describedby="voice-hotwords-help voice-hotwords-error"
                aria-invalid={Boolean(hotwordError)}
                onChange={(event) => setHotwords(event.target.value)}
                placeholder={t('settings.hotwordsPlaceholder')}
                className="ui-textarea"
              />
              <p id="voice-hotwords-help" className="text-sm text-fg-secondary">{t('settings.hotwordsHelp')}</p>
              <p id="voice-hotwords-error" role={hotwordError ? 'alert' : undefined} className="ui-error">{hotwordError}</p>
            </div>
            <button type="submit" disabled={saving || Boolean(hotwordError)} className="ui-btn ui-btn-primary">{t('settings.saveRecognition')}</button>
          </form>
          <p className="text-xs text-fg-muted">{t('settings.privacy')}</p>
          {notice && <p role="status" className="flex items-start gap-2 rounded-control bg-success-soft px-3 py-2 text-sm text-success"><CheckCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />{notice}</p>}
        </section>
      )}
    </div>
  );
}
