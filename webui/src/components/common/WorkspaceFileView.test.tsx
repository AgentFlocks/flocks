import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FileListActions, FileListRow, FileListTable, PreviewPanelHeader, WorkspaceFileSplit } from './WorkspaceFileView';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const observers: TestResizeObserver[] = [];
class TestResizeObserver implements ResizeObserver {
  target?: Element;
  constructor(readonly callback: ResizeObserverCallback) { observers.push(this); }
  observe(target: Element) { this.target = target; }
  unobserve() { this.target = undefined; }
  disconnect = vi.fn(() => { this.target = undefined; });
}

function resize(element: HTMLElement, width: number) {
  Object.defineProperty(element, 'clientWidth', { configurable: true, value: width });
  act(() => {
    for (const observer of observers.filter(item => item.target === element)) {
      observer.callback([{ target: element, contentRect: { width } } as ResizeObserverEntry], observer);
    }
  });
}

// MouseEvent supplies coordinates in jsdom even when native PointerEvent is absent.
function pointer(element: HTMLElement | Window, type: string, clientX: number, pointerId = 1) {
  const event = new MouseEvent(type, { bubbles: true, clientX });
  Object.defineProperty(event, 'pointerId', { value: pointerId });
  fireEvent(element, event);
}

const list = (width: number) => <output data-testid="list-width">{width}</output>;
const resizeLabel = 'files.preview.resize';

beforeEach(() => {
  observers.length = 0;
  vi.stubGlobal('ResizeObserver', TestResizeObserver);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('WorkspaceFileSplit', () => {
  it('uses the whole list pane until a preview is selected and removes the pane on close', () => {
    const { container, rerender } = render(<WorkspaceFileSplit list={list} />);
    const split = container.firstElementChild as HTMLElement;
    expect(split.children).toHaveLength(1);
    expect(screen.queryByRole('button', { name: resizeLabel })).not.toBeInTheDocument();
    resize(split, 1200);
    resize(screen.getByTestId('list-width').parentElement!, 1200);
    expect(screen.getByTestId('list-width')).toHaveTextContent('1200');

    rerender(<WorkspaceFileSplit list={list} preview={<div>Selected file</div>} />);
    expect(split.children).toHaveLength(2);
    expect(screen.getByRole('button', { name: resizeLabel }).parentElement).toHaveStyle({ width: '600px', minWidth: '420px' });

    rerender(<WorkspaceFileSplit list={list} />);
    expect(split.children).toHaveLength(1);
    expect(screen.queryByText('Selected file')).not.toBeInTheDocument();
  });

  it('measures the host rather than the viewport and passes the actual list width to its content', () => {
    const { container } = render(<WorkspaceFileSplit list={list} preview={<div>Preview</div>} />);
    const split = container.firstElementChild as HTMLElement;
    const preview = screen.getByRole('button', { name: resizeLabel }).parentElement;
    resize(split, 1400);
    expect(preview).toHaveStyle({ width: '700px' });
    resize(screen.getByTestId('list-width').parentElement!, 684);
    expect(screen.getByTestId('list-width')).toHaveTextContent('684');
    resize(split, 900);
    expect(preview).toHaveStyle({ width: '450px' });
    resize(split, 700);
    expect(split).toHaveAttribute('data-layout', 'stacked');
    expect(preview).toHaveStyle({ width: '100%', minWidth: '0' });
    expect(screen.queryByRole('button', { name: resizeLabel })).not.toBeInTheDocument();
    resize(split, 0);
    expect(preview).toHaveStyle({ width: '100%' });
    resize(split, 1200);
    expect(preview).toHaveStyle({ width: '600px', minWidth: '420px' });
  });

  it('stacks a 375px host with full-width preview controls and restores the desktop split', () => {
    const onClose = vi.fn();
    const { container, rerender } = render(<WorkspaceFileSplit list={list} preview={<button onClick={onClose}>Close preview</button>} />);
    const split = container.firstElementChild as HTMLElement;
    resize(split, 1200);
    const handle = screen.getByRole('button', { name: resizeLabel });
    pointer(handle, 'pointerdown', 600);
    resize(split, 375);
    expect(split).toHaveClass('flex-col', 'overflow-y-auto');
    expect(split).toHaveAttribute('data-layout', 'stacked');
    const preview = screen.getByRole('button', { name: 'Close preview' }).parentElement;
    expect(preview).toHaveStyle({ width: '100%', minWidth: '0', minHeight: '260px' });
    expect(screen.queryByRole('button', { name: resizeLabel })).not.toBeInTheDocument();
    pointer(window, 'pointermove', 400);
    resize(split, 1200);
    expect(preview).toHaveStyle({ width: '420px' });
    resize(split, 375);
    fireEvent.click(screen.getByRole('button', { name: 'Close preview' }));
    expect(onClose).toHaveBeenCalledOnce();
    rerender(<WorkspaceFileSplit list={list} />);
    expect(split.children).toHaveLength(1);
    expect(split).toHaveAttribute('data-layout', 'split');
    expect(split).not.toHaveClass('flex-col');
  });

  it('drags within the original 420px minimum and host-minus-360px maximum and retains the chosen width', () => {
    const { container } = render(<WorkspaceFileSplit list={list} preview={<div>Preview</div>} />);
    const split = container.firstElementChild as HTMLElement;
    const handle = screen.getByRole('button', { name: resizeLabel });
    const preview = handle.parentElement;
    resize(split, 1200);
    pointer(handle, 'pointerdown', 600);
    pointer(window, 'pointermove', 400);
    expect(preview).toHaveStyle({ width: '800px' });
    pointer(window, 'pointermove', -200);
    expect(preview).toHaveStyle({ width: '840px' });
    pointer(window, 'pointermove', 1200);
    expect(preview).toHaveStyle({ width: '420px' });
    pointer(window, 'pointermove', 400);
    pointer(window, 'pointerup', 400);
    pointer(window, 'pointermove', 900);
    expect(preview).toHaveStyle({ width: '800px' });
    resize(split, 1600);
    expect(preview).toHaveStyle({ width: '800px' });
    resize(split, 900);
    expect(preview).toHaveStyle({ width: '540px' });
  });

  it('ignores unrelated pointers and stops dragging on cancellation, preview close, and unmount', () => {
    const removeListener = vi.spyOn(window, 'removeEventListener');
    const { container, rerender, unmount } = render(<WorkspaceFileSplit list={list} preview={<div>Preview</div>} />);
    resize(container.firstElementChild as HTMLElement, 1200);
    const handle = screen.getByRole('button', { name: resizeLabel });
    const preview = handle.parentElement;
    pointer(handle, 'pointerdown', 600);
    pointer(window, 'pointermove', 400, 2);
    expect(preview).toHaveStyle({ width: '600px' });
    pointer(window, 'pointercancel', 600);
    pointer(window, 'pointermove', 400);
    expect(preview).toHaveStyle({ width: '600px' });

    pointer(handle, 'pointerdown', 600);
    rerender(<WorkspaceFileSplit list={list} />);
    pointer(window, 'pointermove', 400);
    rerender(<WorkspaceFileSplit list={list} preview={<div>Preview</div>} />);
    expect(screen.getByRole('button', { name: resizeLabel }).parentElement).toHaveStyle({ width: '600px' });
    pointer(screen.getByRole('button', { name: resizeLabel }), 'pointerdown', 600);
    unmount();
    expect(removeListener).toHaveBeenCalledWith('pointermove', expect.any(Function));
    expect(removeListener).toHaveBeenCalledWith('pointerup', expect.any(Function));
    expect(removeListener).toHaveBeenCalledWith('pointercancel', expect.any(Function));
    expect(observers.every(observer => observer.disconnect.mock.calls.length === 1)).toBe(true);
  });
});

describe('file view presentation', () => {
  it('shares sticky headers, selected rows, and hover/focus actions without triggering row selection', () => {
    const onSelect = vi.fn();
    const onDelete = vi.fn();
    render(<FileListTable aria-label="Files" header={<th>Name</th>}>
      <FileListRow selected onClick={onSelect}>
        <td>notes.md<FileListActions><button onClick={onDelete}>Delete</button></FileListActions></td>
      </FileListRow>
    </FileListTable>);
    expect(screen.getByRole('columnheader', { name: 'Name' }).closest('thead')).toHaveClass('sticky', 'top-0');
    expect(screen.getByText('notes.md').closest('tr')).toHaveClass('bg-slate-100');
    expect(screen.getByRole('button', { name: 'Delete' }).parentElement).toHaveClass('group-hover:opacity-100', 'group-focus-within:opacity-100');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onDelete).toHaveBeenCalledOnce();
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('notes.md'));
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it('uses the same compact preview header and metadata shell with caller-owned actions', () => {
    render(<PreviewPanelHeader title="notes.md" icon={<span>file</span>} actions={<button>Download</button>} meta={<span>24 B</span>} />);
    expect(screen.getByTitle('notes.md').parentElement).toHaveClass('px-4', 'py-2.5', 'border-b');
    expect(screen.getByText('24 B').parentElement).toHaveClass('px-4', 'py-1.5', 'bg-gray-50');
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument();
  });
});
