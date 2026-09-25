import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import PillGroup from './PillGroup';

const options = [{ value: 'one', label: 'One' }, { value: 'two', label: 'Two' }, { value: 'three', label: 'Three' }];

function Selection() {
  const [value, setValue] = useState('one');
  return <PillGroup options={options} value={value} onChange={setValue} />;
}

describe('PillGroup keyboard selection', () => {
  it('moves focus and selection with arrows, Home and End, wrapping at the edge', async () => {
    const user = userEvent.setup();
    render(<Selection />);
    await user.tab();
    expect(screen.getByRole('button', { name: 'One' })).toHaveFocus();
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole('button', { name: 'Three', pressed: true })).toHaveFocus();
    await user.keyboard('{Home}{ArrowRight}');
    expect(screen.getByRole('button', { name: 'Two', pressed: true })).toHaveFocus();
    await user.keyboard('{End}');
    expect(screen.getByRole('button', { name: 'Three', pressed: true })).toHaveFocus();
  });

  it('does not change a disabled selection', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<PillGroup options={options} value="one" onChange={onChange} disabled />);
    await user.click(screen.getByRole('button', { name: 'Two' }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'One', pressed: true })).toBeDisabled();
  });
});
