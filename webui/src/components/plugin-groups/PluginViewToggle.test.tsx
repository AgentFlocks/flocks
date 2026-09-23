import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { I18nextProvider } from 'react-i18next';
import { createInstance, type ResourceKey } from 'i18next';
import { I18N_NAMESPACES, INITIAL_NAMESPACES, readLazyLocale } from '../../i18nResources';
import PluginViewToggle from './PluginViewToggle';

const load = (language: string) => new Promise<ResourceKey>((resolve, reject) => {
  readLazyLocale(language, 'pluginGroups', (error, data) => {
    if (error) reject(error);
    else resolve(data as ResourceKey);
  });
});

describe('PluginViewToggle and lazy group translations', () => {
  it.each([
    ['en-US', 'Card view', 'List view'],
    ['zh-CN', '卡片视图', '列表视图'],
  ])('lazily loads %s labels and exposes compact accessible pressed buttons', async (language, cards, list) => {
    expect(I18N_NAMESPACES).toContain('pluginGroups');
    expect(INITIAL_NAMESPACES).not.toContain('pluginGroups');
    const i18n = createInstance();
    await i18n.init({ lng: language, fallbackLng: false, resources: { [language]: { pluginGroups: await load(language) } } });
    const onChange = vi.fn();
    const { rerender } = render(<I18nextProvider i18n={i18n}><PluginViewToggle value="cards" onChange={onChange} /></I18nextProvider>);
    expect(screen.getByRole('button', { name: cards })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: list })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', { name: list }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith('list');
    rerender(<I18nextProvider i18n={i18n}><PluginViewToggle value="list" onChange={onChange} /></I18nextProvider>);
    expect(screen.getByRole('button', { name: list })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getAllByRole('button')).toHaveLength(2);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
