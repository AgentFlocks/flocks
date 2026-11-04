// Regression: theme provider must not crash when window.localStorage access throws
// (sandboxed frame / storage disabled). PLT-0962.
import { render, screen, act } from '@testing-library/react';
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { useContext } from 'react';
import { ThemeContext, ThemeProvider } from './ThemeContext';

function Probe() {
  const { theme, toggleTheme } = useContext(ThemeContext);
  return <button onClick={toggleTheme}>theme:{theme}</button>;
}

describe('ThemeProvider without localStorage', () => {
  let original: PropertyDescriptor | undefined;
  beforeEach(() => {
    original = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() { throw new Error('storage disabled'); },
    });
  });
  afterEach(() => {
    if (original) Object.defineProperty(window, 'localStorage', original);
  });

  it('renders and toggles in memory', () => {
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByText('theme:light')).toBeTruthy();
    act(() => { screen.getByRole('button').click(); });
    expect(screen.getByText('theme:dark')).toBeTruthy();
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });
});
