import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ConfigPage from './index';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/pages/AdminUsers', () => ({ default: () => <div data-testid="admin-users">User management</div> }));

describe('ConfigPage', () => {
  it('shows user management directly without the duplicate logout action or inner tab', () => {
    render(<ConfigPage />);
    expect(screen.getByRole('heading', { name: 'admin.pageTitle' })).toBeInTheDocument();
    expect(screen.getByTestId('admin-users')).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'actions.logout' })).not.toBeInTheDocument();
    expect(screen.queryByText('socketGateway')).not.toBeInTheDocument();
  });
});
