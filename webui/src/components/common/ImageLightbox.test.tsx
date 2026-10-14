import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ImageLightbox from './ImageLightbox';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

it('closes on Escape and releases the page scroll lock when unmounted', () => {
  const onClose = vi.fn();
  const { unmount } = render(<ImageLightbox src="/a.png" alt="diagram" onClose={onClose} />);
  expect(document.body.style.overflow).toBe('hidden');
  expect(within(document.body).getByRole('img', { name: 'diagram' })).toBeInTheDocument();
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(onClose).toHaveBeenCalledTimes(1);
  unmount();
  expect(document.body.style.overflow).toBe('');
});

it('keeps the scroll lock until the last of two previews closes', () => {
  const first = render(<ImageLightbox src="/a.png" onClose={vi.fn()} />);
  const second = render(<ImageLightbox src="/b.png" onClose={vi.fn()} />);
  first.unmount();
  expect(document.body.style.overflow).toBe('hidden');
  second.unmount();
  expect(document.body.style.overflow).toBe('');
});
