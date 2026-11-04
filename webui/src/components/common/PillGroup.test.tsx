import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import PillGroup from './PillGroup';

const options = [
  { value: 'one', label: 'One', activeClass: 'bg-slate-700 text-white border-slate-700' },
  { value: 'two', label: 'Two', activeClass: 'bg-slate-700 text-white border-slate-700' },
  { value: 'three', label: 'Three', activeClass: 'bg-slate-700 text-white border-slate-700' },
];

function Selection() {
  const [value, setValue] = useState('one');
  return <PillGroup options={options} value={value} onChange={setValue} />;
}

describe('PillGroup selection', () => {
  it('marks the selected pill with its active class and switches on click', async () => {
    const user = userEvent.setup();
    render(<Selection />);
    expect(screen.getByRole('button', { name: 'One' })).toHaveClass('bg-slate-700');
    await user.click(screen.getByRole('button', { name: 'Three' }));
    expect(screen.getByRole('button', { name: 'Three' })).toHaveClass('bg-slate-700');
    expect(screen.getByRole('button', { name: 'One' })).not.toHaveClass('bg-slate-700');
  });

  it('does not change a disabled selection', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<PillGroup options={options} value="one" onChange={onChange} disabled />);
    await user.click(screen.getByRole('button', { name: 'Two' }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
