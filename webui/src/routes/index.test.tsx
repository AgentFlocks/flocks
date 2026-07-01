import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { LegacyWebUIContractPageRedirect } from './index';

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
