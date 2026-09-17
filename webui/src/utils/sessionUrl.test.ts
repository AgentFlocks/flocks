import { describe, expect, it } from 'vitest';
import { safeReturnTo, sessionPath } from './sessionUrl';

describe('session links', () => {
  it('encodes the ID as a single path segment', () => {
    expect(sessionPath('ses/a?b#c')).toBe('/sessions/ses%2Fa%3Fb%23c');
  });
  it.each([null, '', 'https://evil.test', '//evil.test', '/%2fevil.test', '/%5cevil.test', '/login', '/login?returnTo=/login', '/setup-admin', '/%6cogin', '/a/../login', '/%zz'])('rejects unsafe returnTo %s', (value) => {
    expect(safeReturnTo(value)).toBe('/');
  });
  it('preserves legacy messages with spaces, newlines and URLs in query values', () => {
    const link = '/sessions?session=ses_a&message=' + encodeURIComponent('hello world\nhttps://example.test/a');
    expect(safeReturnTo(link)).toBe(link);
  });
  it('preserves the session and focus target', () => {
    expect(safeReturnTo('/sessions/ses_a?focusMessage=msg_a#message')).toBe('/sessions/ses_a?focusMessage=msg_a#message');
  });
});
