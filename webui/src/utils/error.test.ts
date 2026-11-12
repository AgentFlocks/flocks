import { describe, expect, it } from 'vitest';
import { extractErrorMessage, isServiceRefusal } from './error';

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

  it('shows field-level details from the production validation error envelope', () => {
    expect(extractErrorMessage({ response: { data: {
      error: 'ValidationError', message: 'Request validation failed',
      details: [{ loc: ['body', 'models', 0, 'name'], msg: 'Invalid model name', ctx: { error: 'Invalid model name' } }],
    } } })).toBe('models.0.name: Invalid model name');
  });

  it('serializes non-string validation messages without returning objects', () => {
    expect(extractErrorMessage({ response: { data: { detail: [{ msg: { nested: true } }] } } }))
      .toBe('{"msg":{"nested":true}}');
  });
});

describe('isServiceRefusal', () => {
  // nginx's own page, as a gateway set up that way sends it for a service it cannot reach.
  const proxyPage = '<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n<center><h1>404 Not Found</h1></center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n';

  it.each([
    ['its exception handler', { response: { status: 409, data: { error: 'HTTPException', message: '这台机器不是发布包安装的' } } }],
    ['its sign-in check', { response: { status: 401, data: { error: 'AuthError', message: '请先登录' } } }],
    ['a detail that is not text', { response: { status: 404, data: { error: 'HTTPException', message: { code: 'gone' } } } }],
    ['the framework', { response: { status: 403, data: { detail: '请重新登录' } } }],
  ])('takes a 4xx in the shape of %s for the service saying no', (_name, err) => {
    expect(isServiceRefusal(err)).toBe(true);
  });

  it.each([
    ['a proxy page under 404', { response: { status: 404, data: proxyPage } }],
    ['a proxy page under 403', { response: { status: 403, data: '<html><body>403 Forbidden</body></html>' } }],
    ['an empty 404', { response: { status: 404, data: '' } }],
    ['a 404 with no body', { response: { status: 404 } }],
    ['a gateway message in JSON', { response: { status: 404, data: { message: 'no Route matched with those values' } } }],
    ['a list', { response: { status: 400, data: ['bad'] } }],
    ['a 502 page', { response: { status: 502, data: '<html><body>502 Bad Gateway</body></html>' } }],
    ['a server fault in JSON', { response: { status: 500, data: { error: 'InternalServerError', message: 'boom' } } }],
    ['no answer', new Error('Network Error')],
    ['nothing', null],
  ])('does not take %s for the service saying no', (_name, err) => {
    expect(isServiceRefusal(err)).toBe(false);
  });
});
