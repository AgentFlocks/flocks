import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import PageHeader from './PageHeader';

describe('PageHeader', () => {
  it('renders title and description', () => {
    render(
      <PageHeader
        title="Tasks"
        description="Manage scheduled work"
      />,
    );
    expect(screen.getByRole('heading', { name: 'Tasks' })).toBeInTheDocument();
    expect(screen.getByText('Manage scheduled work')).toBeInTheDocument();
  });

  it('renders toolbar slot when provided', () => {
    render(
      <PageHeader
        title="Workflows"
        toolbar={<button type="button">Filter</button>}
      />,
    );
    expect(screen.getByRole('button', { name: 'Filter' })).toBeInTheDocument();
  });

  it('renders status badge in header row', () => {
    render(
      <PageHeader
        title="Editor"
        status={<span data-testid="status">Valid</span>}
      />,
    );
    expect(screen.getByTestId('status')).toBeInTheDocument();
  });
});
