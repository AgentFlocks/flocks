import { describe, it, expect } from 'vitest';
import { extractErrorMessage } from '@/utils/error';

// Task page toasts must surface the backend detail, not axios' generic
// "Request failed with status code 422".
describe('Task page error surfacing', () => {
  it('shows the backend cron validation detail', () => {
    const err = Object.assign(new Error('Request failed with status code 422'), {
      response: { data: { error: 'HTTPException', message: 'Invalid cron expression: only 5-field cron is supported' } },
    });
    expect(extractErrorMessage(err)).toBe('Invalid cron expression: only 5-field cron is supported');
  });

  it('falls back to the error message when no backend body', () => {
    expect(extractErrorMessage(new Error('Network Error'))).toBe('Network Error');
  });
});
