import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import ChatGuideDock, { type ChatGuideAction } from './ChatGuideDock';

const actions: ChatGuideAction[] = Array.from({ length: 6 }, (_item, index) => ({
  label: `Guide ${index + 1}`,
  description: `Description ${index + 1}`,
  prompt: `Prompt ${index + 1}`,
  group: index < 3 ? 'Create Guides' : 'Create Cases',
}));

describe('ChatGuideDock', () => {
  it('keeps guides in a compact rail and expands upward to a full guide panel', async () => {
    const user = userEvent.setup();
    const onStartPrompt = vi.fn();

    render(
      <ChatGuideDock
        actions={actions}
        collapseTitle="Collapse guide"
        expandTitle="Expand guide"
        onStartPrompt={onStartPrompt}
      />,
    );

    expect(screen.getByRole('button', { name: 'Guide 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Guide 5' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Guide 6' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('chat-guide-expanded-panel')).not.toBeInTheDocument();

    const expandButton = screen.getByRole('button', { name: /Expand guide/ });
    expect(expandButton).toHaveAttribute('aria-expanded', 'false');

    await user.click(expandButton);

    expect(screen.getByTestId('chat-guide-expanded-panel')).toBeInTheDocument();
    expect(screen.getByText('Create Guides')).toBeInTheDocument();
    expect(screen.getByText('Create Cases')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Guide 6' })).toBeInTheDocument();
    expect(expandButton).toHaveAttribute('aria-expanded', 'true');

    await user.click(screen.getByRole('button', { name: 'Guide 6' }));

    expect(onStartPrompt).toHaveBeenCalledWith('Prompt 6', 'Guide 6');
  });

  it('keeps a left-side collapse control for the guide rail', async () => {
    const user = userEvent.setup();

    render(
      <ChatGuideDock
        actions={actions}
        collapseTitle="Collapse guide"
        expandTitle="Expand guide"
        onStartPrompt={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Collapse guide' }));

    expect(screen.queryByRole('button', { name: 'Guide 1' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Expand guide' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes the expanded panel when clicking outside the guide dock', async () => {
    const user = userEvent.setup();

    render(
      <ChatGuideDock
        actions={actions}
        collapseTitle="Collapse guide"
        expandTitle="Expand guide"
        onStartPrompt={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: /Expand guide/ }));
    expect(screen.getByTestId('chat-guide-expanded-panel')).toBeInTheDocument();

    await user.click(document.body);

    expect(screen.queryByTestId('chat-guide-expanded-panel')).not.toBeInTheDocument();
  });
});
