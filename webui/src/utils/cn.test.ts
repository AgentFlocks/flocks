import { describe, expect, it } from 'vitest';
import { cn } from './cn';

describe('cn', () => {
  it('merges conflicting tailwind classes', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4');
  });

  it('handles conditional classes', () => {
    expect(cn('text-ink', false && 'hidden', 'font-medium')).toBe('text-ink font-medium');
  });
});
