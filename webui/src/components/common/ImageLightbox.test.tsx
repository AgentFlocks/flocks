import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ImageLightbox from './ImageLightbox';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

it('closes only the top preview and retains the scroll lock until all previews close', () => {
  const firstClose = vi.fn();
  const secondClose = vi.fn();
  const previousOverflow = document.body.style.overflow;
  const first = <ImageLightbox key="first" src="first.png" alt="First preview" onClose={firstClose} />;
  const { rerender, unmount } = render(<>{first}<ImageLightbox src="second.png" alt="Second preview" onClose={secondClose} /></>);
  const topClose = within(screen.getByRole('dialog', { name: 'Second preview' })).getByRole('button', { name: 'button.close' });
  expect(topClose).toHaveFocus();
  fireEvent.keyDown(topClose, { key: 'Tab' });
  expect(topClose).toHaveFocus();
  fireEvent.keyDown(topClose, { key: 'Escape' });
  expect(secondClose).toHaveBeenCalledTimes(1);
  expect(firstClose).not.toHaveBeenCalled();
  rerender(<>{first}</>);
  expect(document.body.style.overflow).toBe('hidden');
  const remainingClose = screen.getByRole('button', { name: 'button.close' });
  expect(remainingClose).toHaveFocus();
  fireEvent.keyDown(remainingClose, { key: 'Escape' });
  expect(firstClose).toHaveBeenCalledTimes(1);
  unmount();
  expect(document.body.style.overflow).toBe(previousOverflow);
});
