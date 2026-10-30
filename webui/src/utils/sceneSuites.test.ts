import { describe, expect, it } from 'vitest';

import { sceneSuiteErrorMessage } from './sceneSuites';

function httpError(data: unknown) {
  return Object.assign(new Error('Request failed with status code 422'), { response: { data } });
}

describe('sceneSuiteErrorMessage', () => {
  it('shows the reason the server gave, in either of its shapes', () => {
    expect(sceneSuiteErrorMessage(httpError({ detail: '该套件需要单独授权' }), 'x')).toBe('该套件需要单独授权');
    expect(sceneSuiteErrorMessage(httpError({ detail: { code: 'c', message: '需要确认' } }), 'x')).toBe('需要确认');
    expect(sceneSuiteErrorMessage(
      httpError({ error: 'HTTPException', message: '「ai-redteam-workspace」1.1.0 需要新的授权' }),
      'x',
    )).toBe('「ai-redteam-workspace」1.1.0 需要新的授权');
  });

  it('falls back to the error itself, then to the fallback', () => {
    expect(sceneSuiteErrorMessage(httpError({ message: '  ' }), 'x')).toBe('Request failed with status code 422');
    expect(sceneSuiteErrorMessage(null, '操作失败')).toBe('操作失败');
  });
});
