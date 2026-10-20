import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { findActiveTabHref } from '@/utils/layoutTabs';
import KeepAlivePanes from './KeepAlivePanes';

const TAB_HREFS = ['/sessions', '/tasks'];
const routes = [
  { path: 'sessions/:sessionId?', element: <div data-testid="session-page" /> },
  { path: 'tasks', element: <div data-testid="tasks-page" /> },
  { path: '*', element: <div /> },
];

function Tabs() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate('/tasks')}>open-tasks</button>
      <KeepAlivePanes
        panes={TAB_HREFS.map((href) => ({ href }))}
        activeHref={findActiveTabHref(TAB_HREFS, location.pathname)}
        location={location}
        routes={routes}
      />
    </>
  );
}

describe('KeepAlivePanes', () => {
  it('clips every pane so a hidden page cannot scroll the shared content area', () => {
    render(
      <MemoryRouter initialEntries={['/sessions/session-1']}>
        <Tabs />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText('open-tasks'));

    const sessionPane = screen.getByTestId('session-page').closest('[data-keep-alive-pane]');
    const tasksPane = screen.getByTestId('tasks-page').closest('[data-keep-alive-pane]');
    expect(sessionPane).toHaveAttribute('data-keep-alive-pane', 'inactive');
    expect(tasksPane).toHaveAttribute('data-keep-alive-pane', 'active');

    // Both panes sit in one root. It must clip (no scroll container, not even
    // a programmatic one) so a tall hidden pane leaves no scrollable overflow
    // for <main>, which a background scrollIntoView would otherwise scroll.
    const root = sessionPane?.parentElement;
    expect(root).toBe(tasksPane?.parentElement);
    expect(root).toHaveClass('overflow-clip');
    expect(root?.className).not.toMatch(/\boverflow-(?:y-|x-)?(?:hidden|auto|scroll)\b/);
  });
});
