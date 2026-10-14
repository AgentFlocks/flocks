import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PageHeader from '@/components/common/PageHeader';

describe('PageHeader', () => {
  it('renders title, description, icon and action', () => {
    render(
      <PageHeader
        title="设备接入"
        description="管理设备"
        icon={<span data-testid="icon" />}
        action={<button type="button">添加设备</button>}
      />,
    );

    expect(screen.getByRole('heading', { level: 1, name: '设备接入' })).toBeInTheDocument();
    expect(screen.getByText('管理设备')).toBeInTheDocument();
    expect(screen.getByTestId('icon')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '添加设备' })).toBeInTheDocument();
  });

  it('omits the action slot when no action is given', () => {
    const { container } = render(<PageHeader title="系统日志" />);

    expect(container.querySelector('.ml-auto')).toBeNull();
    expect(screen.queryByText('管理设备')).not.toBeInTheDocument();
  });
});
