import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ExtensionSlot, { __resetExtensionSlotCacheForTesting } from './ExtensionSlot';

const { registry } = vi.hoisted(() => ({
  registry: {
    routes: [] as unknown[],
    navItems: [] as unknown[],
    settingsSections: [] as unknown[],
    slots: [] as Array<{ id: string; slot: string; order?: number; component: (props: { context: Record<string, unknown> }) => JSX.Element }>,
    i18n: {},
  },
}));

vi.mock('@ext/registry', () => ({ default: registry }));

describe('ExtensionSlot', () => {
  beforeEach(() => {
    registry.slots = [];
    __resetExtensionSlotCacheForTesting();
  });

  it('renders the default content when nothing is registered', () => {
    render(
      <ExtensionSlot name="settings.account">
        <span>default account list</span>
      </ExtensionSlot>,
    );
    expect(screen.getByText('default account list')).toBeInTheDocument();
  });

  it('renders nothing when nothing is registered and no default is given', () => {
    const { container } = render(<ExtensionSlot name="session.settings" context={{ sessionId: 's1' }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders registered components in order with the slot context', () => {
    registry.slots = [
      {
        id: 'second',
        slot: 'session.settings',
        order: 20,
        component: ({ context }) => <div>second:{String(context.sessionId)}</div>,
      },
      {
        id: 'first',
        slot: 'session.settings',
        order: 10,
        component: ({ context }) => <div>first:{String(context.sessionId)}</div>,
      },
      {
        id: 'other-slot',
        slot: 'channel.settings',
        component: () => <div>channel</div>,
      },
    ];

    const { container } = render(
      <ExtensionSlot name="session.settings" context={{ sessionId: 's1' }}>
        <span>default</span>
      </ExtensionSlot>,
    );

    expect(container.textContent).toBe('first:s1second:s1');
    expect(screen.queryByText('default')).not.toBeInTheDocument();
    expect(screen.queryByText('channel')).not.toBeInTheDocument();
  });
});
