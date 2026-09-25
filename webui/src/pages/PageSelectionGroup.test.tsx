import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import PageSelectionGroup from './PageSelectionGroup';

describe('PageSelectionGroup', () => {
  it('activates choices with arrows and Home/End while skipping disabled items', async () => {
    const user = userEvent.setup();
    function Example() {
      const [choice, setChoice] = useState('Light');
      return <PageSelectionGroup aria-label="Appearance">
        {['Light', 'Unavailable', 'Dark', 'System'].map(label => (
          <button key={label} disabled={label === 'Unavailable'} aria-pressed={choice === label} onClick={() => setChoice(label)}>{label}</button>
        ))}
      </PageSelectionGroup>;
    }
    render(<Example />);
    await user.click(screen.getByRole('button', { name: 'Light' }));
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('button', { name: 'Dark' })).toHaveFocus();
    expect(screen.getByRole('button', { name: 'Dark' })).toHaveAttribute('aria-pressed', 'true');
    await user.keyboard('{End}');
    expect(screen.getByRole('button', { name: 'System' })).toHaveAttribute('aria-pressed', 'true');
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('button', { name: 'Light' })).toHaveFocus();
    await user.keyboard('{Home}');
    expect(screen.getByRole('button', { name: 'Light' })).toHaveAttribute('aria-pressed', 'true');
  });
});
