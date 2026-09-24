import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { AuthReturnRedirect, LoginRedirect, LegacyWebUIContractPageRedirect } from './index';

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</div>;
}

describe('LegacyWebUIContractPageRedirect', () => {
  it('preserves nested path, query, and hash state', async () => {
    render(
      <MemoryRouter initialEntries={['/user-defined-pages/alert-dashboard/detail?status=open#row-1']}>
        <Routes>
          <Route path="/user-defined-pages/:pageId/*" element={<LegacyWebUIContractPageRedirect />} />
          <Route path="/contracts/webui/:pageId/*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('location')).toHaveTextContent(
      '/contracts/webui/alert-dashboard/detail?status=open#row-1',
    );
  });
});

describe('authentication return navigation', () => {
  it('preserves the deep link while redirecting to login', async () => {
    render(<MemoryRouter initialEntries={['/sessions/ses_a?focusMessage=msg_a']}><Routes>
      <Route path="/login" element={<LocationProbe />} />
      <Route path="*" element={<LoginRedirect />} />
    </Routes></MemoryRouter>);
    expect(await screen.findByTestId('location')).toHaveTextContent('/login?returnTo=%2Fsessions%2Fses_a%3FfocusMessage%3Dmsg_a');
  });
  it('returns to the session after login, including a refreshed login page', async () => {
    render(<MemoryRouter initialEntries={['/login?returnTo=%2Fsessions%2Fses_a%3FfocusMessage%3Dmsg_a']}><Routes>
      <Route path="/login" element={<AuthReturnRedirect />} />
      <Route path="*" element={<LocationProbe />} />
    </Routes></MemoryRouter>);
    expect(await screen.findByTestId('location')).toHaveTextContent('/sessions/ses_a?focusMessage=msg_a');
  });
});
