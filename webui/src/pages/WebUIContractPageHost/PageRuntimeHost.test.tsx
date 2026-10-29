import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { setupSSEMock } from '@/test/mocks/sse';

vi.mock('@/api/webuiContractPages', () => ({
  webuiContractPagesAPI: { get: vi.fn() },
}));

import i18n from '@/i18n';
import PageRuntimeHost from './PageRuntimeHost';

// OPS-0458: rendering the host without a page id shows the localized hint.
describe('PageRuntimeHost without pageId', () => {
  setupSSEMock();

  it('shows the zh-CN hint', async () => {
    await i18n.changeLanguage('zh-CN');
    render(<PageRuntimeHost />);
    expect(await screen.findByText('未指定页面 ID')).toBeInTheDocument();
  });

  it('shows the en-US hint', async () => {
    await i18n.changeLanguage('en-US');
    const expected = i18n.t('host.missingPageId', { ns: 'webuiContractPage' });
    expect(expected).not.toBe('host.missingPageId');
    expect(expected).toBe('Page ID is missing');
    render(<PageRuntimeHost />);
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });
});
