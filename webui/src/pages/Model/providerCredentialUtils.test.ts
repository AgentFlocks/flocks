import { describe, expect, it } from 'vitest';

import { hasPendingProviderCredentialChanges } from './providerCredentialUtils';

describe('hasPendingProviderCredentialChanges', () => {
  it('returns false when api key and base url are unchanged', () => {
    expect(
      hasPendingProviderCredentialChanges(
        { apiKey: 'same-key', baseUrl: 'https://example.com/v1' },
        { apiKey: 'same-key', baseUrl: 'https://example.com/v1' },
      ),
    ).toBe(false);
  });

  it('returns true when only the base url changes', () => {
    expect(
      hasPendingProviderCredentialChanges(
        { apiKey: 'same-key', baseUrl: 'https://old.example.com/v1' },
        { apiKey: 'same-key', baseUrl: 'https://qianfan.baidubce.com/v2/coding' },
      ),
    ).toBe(true);
  });

  it('ignores whitespace-only differences', () => {
    expect(
      hasPendingProviderCredentialChanges(
        { apiKey: 'same-key', baseUrl: 'https://example.com/v1' },
        { apiKey: ' same-key ', baseUrl: '  https://example.com/v1  ' },
      ),
    ).toBe(false);
  });
});
