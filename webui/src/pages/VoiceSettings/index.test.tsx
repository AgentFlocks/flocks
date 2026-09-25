import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import VoiceSettings from './index';

const api = vi.hoisted(() => ({ settings: vi.fn(), updateSettings: vi.fn(), t: (key: string) => key }));
vi.mock('@/api/voice', () => ({ voiceApi: api, VOICE_STATUS_CHANGED: 'flocks-voice-status-changed' }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: api.t }) }));
vi.mock('@/components/common/PageHeader', () => ({ default: ({ title }: { title: string }) => <h1>{title}</h1> }));

const disabled = { configured: false, enabled: false, available: false, credential_source: 'none', resource_id: 'test', model: 'test', capture_mode: 'voice', hotwords: [] };

describe('voice settings opt-in', () => {
  beforeEach(() => { vi.clearAllMocks(); api.settings.mockResolvedValue(disabled); });

  it('requires a key and saves it without sending an enable request', async () => {
    api.updateSettings.mockResolvedValue({ ...disabled, configured: true, credential_source: 'stored' }); // secret-guard: allow
    render(<VoiceSettings />);
    const toggle = await screen.findByRole('switch');
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    const input = screen.getByLabelText('settings.key');
    expect(input).toHaveAttribute('type', 'password');
    fireEvent.change(input, { target: { value: 'test-key-value' } });
    fireEvent.click(screen.getByRole('button', { name: 'settings.saveKey' }));
    await waitFor(() => expect(api.updateSettings).toHaveBeenCalledWith({ api_key: 'test-key-value' })); // secret-guard: allow
    await waitFor(() => expect(toggle).not.toBeDisabled());
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(input).toHaveValue('');
    expect(api.updateSettings).toHaveBeenCalledOnce();
  });

  it('enables only after an explicit click and clears the stored key with an empty value', async () => {
    api.settings.mockResolvedValue({ ...disabled, configured: true, credential_source: 'stored' }); // secret-guard: allow
    api.updateSettings.mockResolvedValueOnce({ ...disabled, configured: true, enabled: true, available: true, credential_source: 'stored' }); // secret-guard: allow
    api.updateSettings.mockResolvedValueOnce(disabled);
    render(<VoiceSettings />);
    fireEvent.click(await screen.findByRole('switch'));
    await waitFor(() => expect(api.updateSettings).toHaveBeenNthCalledWith(1, { enabled: true }));
    await waitFor(() => expect(screen.getByRole('switch')).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: 'settings.clearKey' }));
    await waitFor(() => expect(api.updateSettings).toHaveBeenNthCalledWith(2, { api_key: '' }));
    await waitFor(() => expect(screen.getByRole('switch')).toBeDisabled());
  });

  it('keeps the current setting when saving fails and never displays stored key values', async () => {
    api.settings.mockResolvedValue({ ...disabled, configured: true, credential_source: 'environment' }); // secret-guard: allow
    api.updateSettings.mockRejectedValue(new Error('network'));
    render(<VoiceSettings />);
    fireEvent.click(await screen.findByRole('switch'));
    expect(await screen.findByRole('alert')).toHaveTextContent('settings.saveError');
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByLabelText('settings.key')).toHaveValue('');
    expect(screen.queryByRole('button', { name: 'settings.clearKey' })).not.toBeInTheDocument();
  });

  it('shows a load failure instead of offering an unsafe default toggle', async () => {
    api.settings.mockRejectedValue(new Error('forbidden'));
    render(<VoiceSettings />);
    expect(await screen.findByRole('alert')).toHaveTextContent('settings.loadError');
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
  });

  it('shows fixed local copy rather than reflecting credential details from a failed settings response', async () => {
    api.settings.mockResolvedValue({ ...disabled, configured: true, credential_source: 'stored' }); // secret-guard: allow
    api.updateSettings.mockRejectedValue({ response: { data: { detail: 'private credential details' } } });
    render(<VoiceSettings />);
    fireEvent.click(await screen.findByRole('switch'));
    expect(await screen.findByRole('alert')).toHaveTextContent('settings.saveError');
    expect(screen.queryByText('private credential details')).not.toBeInTheDocument();
  });

  it('loads and saves capture mode and deduplicated hotwords without changing the key or enable switch', async () => {
    api.settings.mockResolvedValue({ ...disabled, capture_mode: 'voice', hotwords: ['Flocks'] });
    api.updateSettings.mockResolvedValue({ ...disabled, capture_mode: 'raw', hotwords: ['Flocks', '张伟', '项目名称'] });
    const changed = vi.fn();
    window.addEventListener('flocks-voice-status-changed', changed);
    try {
      render(<VoiceSettings />);
      const mode = await screen.findByLabelText('settings.captureMode');
      expect(mode).toHaveValue('voice');
      const hotwords = screen.getByLabelText('settings.hotwords');
      expect(hotwords).toHaveValue('Flocks');
      fireEvent.change(mode, { target: { value: 'raw' } });
      fireEvent.change(hotwords, { target: { value: ' Flocks\n张伟，项目名称\nFlocks\n' } });
      fireEvent.click(screen.getByRole('button', { name: 'settings.saveRecognition' }));
      await waitFor(() => expect(api.updateSettings).toHaveBeenCalledWith({ capture_mode: 'raw', hotwords: ['Flocks', '张伟', '项目名称'] }));
      await waitFor(() => expect(changed).toHaveBeenCalledOnce());
      expect(hotwords).toHaveValue('Flocks\n张伟\n项目名称');
    } finally {
      window.removeEventListener('flocks-voice-status-changed', changed);
    }
  });

  it.each([
    ['word'.repeat(17), 'settings.hotwordTooLong'],
    [Array.from({ length: 101 }, (_, index) => `name-${index}`).join('\n'), 'settings.hotwordsTooMany'],
  ])('prevents sending invalid vocabulary (%s)', async (value, error) => {
    render(<VoiceSettings />);
    fireEvent.change(await screen.findByLabelText('settings.hotwords'), { target: { value } });
    expect(screen.getByRole('alert')).toHaveTextContent(error);
    expect(screen.getByRole('button', { name: 'settings.saveRecognition' })).toBeDisabled();
    expect(api.updateSettings).not.toHaveBeenCalled();
  });
});
