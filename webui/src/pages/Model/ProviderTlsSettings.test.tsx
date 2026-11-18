import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import ProviderTlsSettings, {
  DEFAULT_PROVIDER_TLS,
  allTlsRequestFields,
  savedProviderTls,
  tlsRequestFields,
  type ProviderTlsValue,
} from './ProviderTlsSettings';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en-US' } }) }));

function Harness({ initial = DEFAULT_PROVIDER_TLS, onChange }: { initial?: ProviderTlsValue; onChange?: (value: ProviderTlsValue) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <ProviderTlsSettings
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

const PEM = '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----';

it('checks certificates by default and offers a CA certificate', () => {
  render(<Harness />);
  const toggle = screen.getByRole('switch', { name: 'form.tls.verify' });
  expect(toggle).toHaveAttribute('aria-checked', 'true');
  expect(toggle).toHaveAccessibleDescription('form.tls.verifyHint');
  expect(screen.getByLabelText(/form\.tls\.caCert/)).toHaveValue('');
  expect(screen.queryByRole('note')).not.toBeInTheDocument();
});

it('warns what turning the checks off also allows, and hides the CA field meanwhile', async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(<Harness initial={{ verifySsl: true, caCert: PEM }} onChange={onChange} />);

  await user.click(screen.getByRole('switch', { name: 'form.tls.verify' }));

  expect(onChange).toHaveBeenLastCalledWith({ verifySsl: false, caCert: PEM });
  expect(screen.getByRole('switch', { name: 'form.tls.verify' })).toHaveAttribute('aria-checked', 'false');
  expect(screen.getByRole('note')).toHaveTextContent('form.tls.disabledWarning');
  expect(screen.queryByLabelText(/form\.tls\.caCert/)).not.toBeInTheDocument();

  // Turning them back on keeps the certificate that was there.
  await user.click(screen.getByRole('switch', { name: 'form.tls.verify' }));
  expect(screen.getByLabelText(/form\.tls\.caCert/)).toHaveValue(PEM);
});

it('reports a pasted certificate', async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  await user.type(screen.getByLabelText(/form\.tls\.caCert/), '/etc/pki/corp-ca.pem');
  expect(onChange).toHaveBeenLastCalledWith({ verifySsl: true, caCert: '/etc/pki/corp-ca.pem' });
});

it('cannot be changed while disabled', async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(<ProviderTlsSettings value={DEFAULT_PROVIDER_TLS} onChange={onChange} disabled />);
  await user.click(screen.getByRole('switch', { name: 'form.tls.verify' }));
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByLabelText(/form\.tls\.caCert/)).toBeDisabled();
});

describe('request fields', () => {
  it('reads the saved settings, defaults when there are none', () => {
    expect(savedProviderTls(null)).toEqual(DEFAULT_PROVIDER_TLS);
    expect(savedProviderTls({ verify_ssl: null, ca_cert: null })).toEqual(DEFAULT_PROVIDER_TLS);
    expect(savedProviderTls({ verify_ssl: false, ca_cert: PEM })).toEqual({ verifySsl: false, caCert: PEM });
  });

  it('sends only what changed, so stored values stay as saved', () => {
    const saved = { verifySsl: true, caCert: '{env:CORP_CA}' };
    expect(tlsRequestFields(saved, saved)).toEqual({});
    expect(tlsRequestFields({ ...saved, caCert: ' {env:CORP_CA}\n' }, saved)).toEqual({});
    expect(tlsRequestFields({ ...saved, verifySsl: false }, saved)).toEqual({ verify_ssl: false });
    expect(tlsRequestFields({ ...saved, caCert: '' }, saved)).toEqual({ ca_cert: '' });
    expect(tlsRequestFields({ verifySsl: true, caCert: `  ${PEM}\n` })).toEqual({ ca_cert: PEM });
    expect(tlsRequestFields(DEFAULT_PROVIDER_TLS)).toEqual({});
  });

  it('sends every setting for a new provider, defaults included', () => {
    expect(allTlsRequestFields(DEFAULT_PROVIDER_TLS)).toEqual({ verify_ssl: true, ca_cert: '' });
    expect(allTlsRequestFields({ verifySsl: false, caCert: ` ${PEM}\n` })).toEqual({ verify_ssl: false, ca_cert: PEM });
  });
});
