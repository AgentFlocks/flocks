import { describe, expect, it } from 'vitest';
import { ADDITIONAL_ALLOWED_HOSTS_ENV, getAdditionalAllowedHosts } from './viteHosts.ts';

describe('vite host helpers', () => {
  it('keeps Vite defaults when no extra hosts are configured', () => {
    expect(getAdditionalAllowedHosts({})).toBeUndefined();
  });

  it('returns unique configured custom hosts', () => {
    expect(getAdditionalAllowedHosts({
      [ADDITIONAL_ALLOWED_HOSTS_ENV]: 'preview.example.com, .example.org, preview.example.com',
    })).toEqual(['preview.example.com', '.example.org']);
  });

  it('ignores empty custom host entries', () => {
    expect(getAdditionalAllowedHosts({
      [ADDITIONAL_ALLOWED_HOSTS_ENV]: ' , custom.example.com ,, .example.org ',
    })).toEqual(['custom.example.com', '.example.org']);
  });

  it('returns undefined when the extra-host list is empty after trimming', () => {
    expect(getAdditionalAllowedHosts({
      [ADDITIONAL_ALLOWED_HOSTS_ENV]: ' , ,, ',
    })).toBeUndefined();
  });
});
