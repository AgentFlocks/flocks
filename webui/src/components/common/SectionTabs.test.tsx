import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import SectionTabs from './SectionTabs';

describe('SectionTabs', () => {
  it('calls onChange when tab clicked', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <SectionTabs
        activeKey="a"
        onChange={onChange}
        items={[
          { key: 'a', label: 'Tab A' },
          { key: 'b', label: 'Tab B', count: 3 },
        ]}
      />,
    );
    await user.click(screen.getByRole('tab', { name: /Tab B/i }));
    expect(onChange).toHaveBeenCalledWith('b');
  });

  it('shows count badge when count > 0', () => {
    render(
      <SectionTabs
        activeKey="a"
        onChange={() => {}}
        items={[{ key: 'a', label: 'One', count: 5 }]}
      />,
    );
    expect(screen.getByText('5')).toBeInTheDocument();
  });
});
