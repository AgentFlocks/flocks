import { describe, expect, it } from 'vitest';
import { extractErrorMessage } from './error';

describe('extractErrorMessage', () => {
  it.each([
    [{ response: { data: { detail: 'Denied', message: 'Other' } } }, 'Denied'],
    [{ response: { data: { message: 'Backend message' } } }, 'Backend message'],
    [new Error('Network error'), 'Network error'],
    ['Plain error', 'Plain error'],
    [{ response: { data: { detail: [{ msg: 'Invalid path' }, 'Not allowed'] } } }, 'Invalid path; Not allowed'],
    [{ response: { data: { detail: { nested: true }, message: { invalid: true } } }, message: 'Transport error' }, 'Transport error'],
    [{ response: { data: { message: ['invalid'] } }, message: 42 }, 'Fallback'],
    [{ response: { data: { detail: [null, undefined] } } }, 'Fallback'],
    [null, 'Fallback'],
    [undefined, 'Fallback'],
    [{ message: '  ' }, 'Fallback'],
  ])('always returns a renderable string for %j', (error, expected) => {
    const message = extractErrorMessage(error, 'Fallback');
    expect(typeof message).toBe('string');
    expect(message).toBe(expected);
  });

  it('does not throw on circular or unserializable validation details', () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(extractErrorMessage({ response: { data: { detail: [circular, 1n, { msg: 'Valid message' }] } } }))
      .toBe('Valid message');
  });

  it('serializes non-string validation messages without returning objects', () => {
    expect(extractErrorMessage({ response: { data: { detail: [{ msg: { nested: true } }] } } }))
      .toBe('{"msg":{"nested":true}}');
  });
});
