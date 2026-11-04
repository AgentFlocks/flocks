import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The pages bind the SDK shim when they are imported, so the runtime must be in place first.
const sdk = vi.hoisted(() => {
  const mocks = { pageGet: vi.fn(), operation: vi.fn() };
  (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = {
    api: {
      page: { get: mocks.pageGet },
      contract: () => ({ operation: mocks.operation }),
    },
  };
  return mocks;
});

import SocOverviewPage from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_overview/src/index';
import SocAlertsPage from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_alerts/src/index';

// Fills that read the same on light and dark surfaces (blue buttons with white text, verdict colours).
const THEME_NEUTRAL_COLOURS = new Set(['#2563eb', '#fff', '#ff4d6d', '#ffb020', '#2ee6a6', '#58a6ff', '#9b8cff']);

function ruleBody(css: string, selector: string) {
  const start = css.indexOf(`${selector} {`);
  expect(start, `missing rule ${selector}`).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf('}', start));
}

function declaredVariables(body: string) {
  return [...body.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]).sort();
}

function pageCss(container: HTMLElement) {
  return [...container.querySelectorAll('style')].map((style) => style.textContent ?? '').join('\n');
}

function localTime(date: Date) {
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function alertIncident() {
  const observedAt = localTime(new Date(Date.now() - 60 * 60 * 1000));
  return {
    id: 'demo-000',
    sourceRecordId: 'demo-000',
    observedAt,
    rawAlerts: 1,
    priority: 'P1',
    triageAttackVerdict: 'attack',
    triageAttackSuccess: 'success',
    reportTitle: 'SQL 注入攻击研判报告',
    reason: 'SQL 注入攻击（演示数据）',
    srcIp: '198.51.100.10',
    ndrRule: 'R-1000',
    request: { method: 'GET', host: 'app0.example.com', uri: '/static/page0.php', payload: '', evidence: ['GET /static/page0.php HTTP/1.1'] },
    response: { statusCode: 403, sample: '<html>demo</html>', evidence: ['HTTP/1.1 200 OK'] },
    conclusion: { verdict: 'attack', summary: 'SQL 注入攻击（演示数据）', recommendation: '' },
    title: 'SQL 注入攻击',
    triageReport: '### 攻击分析结果\n演示数据：请求携带可疑负载。',
    tableCells: {
      time: { value: observedAt },
      threat_name: { value: 'SQL 注入攻击' },
      threat_type: { value: 'Web 攻击' },
      threat_phase: { value: '漏洞利用' },
      direction: { value: 'out' },
      sip: { value: '198.51.100.10' },
      dip: { value: '203.0.113.20' },
      dport: { value: '80' },
      req_http_url: { value: '/static/page0.php' },
      _source_type: { value: 'tdp' },
      net_type: { value: 'http' },
      net_app_proto: { value: 'http' },
    },
  };
}

describe('SOC workspace pages follow the light / dark theme', () => {
  beforeEach(() => {
    sdk.pageGet.mockReset();
    sdk.operation.mockReset();
    window.localStorage.clear();
    window.localStorage.setItem('flocks-language', 'zh-CN');
  });

  it('gives every overview colour a dark value instead of a fixed light one', async () => {
    sdk.pageGet.mockResolvedValue({ data: {} });
    const { container } = render(<SocOverviewPage />);
    await waitFor(() => expect(sdk.pageGet).toHaveBeenCalled());

    const css = pageCss(container);
    const lightPalette = declaredVariables(ruleBody(css, '.soc-overview-root'));
    expect(lightPalette.length).toBeGreaterThan(20);
    expect(declaredVariables(ruleBody(css, '.dark .soc-overview-root'))).toEqual(lightPalette);

    // Outside the palette no rule may pin a colour that only works on a light surface.
    const rules = css.replace(/--soc-[\w-]+\s*:[^;]+;/g, '');
    const literals = [...new Set(rules.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [])].map((colour) => colour.toLowerCase());
    expect(literals.filter((colour) => !THEME_NEUTRAL_COLOURS.has(colour))).toEqual([]);
  });

  it('draws the alert timeline and the verdict stamp with theme variables', async () => {
    sdk.operation.mockResolvedValue({ data: { incidents: [alertIncident()], tableColumns: [], summary: {} } });
    const { container } = render(<SocAlertsPage />);

    const row = await screen.findByText('SQL 注入攻击', { selector: 'td *, td' });
    const css = pageCss(container);
    const lightPalette = declaredVariables(ruleBody(css, '[data-soc-alerts-root]'));
    expect(lightPalette).toEqual(expect.arrayContaining(['--soc-alerts-chart-grid', '--soc-alerts-chart-label', '--soc-alerts-stamp-band']));
    expect(declaredVariables(ruleBody(css, '.dark [data-soc-alerts-root]'))).toEqual(lightPalette);

    const timeline = container.querySelector('svg.h-52') as SVGSVGElement;
    expect(timeline).not.toBeNull();
    const lines = [...timeline.querySelectorAll('line')];
    const labels = [...timeline.querySelectorAll('text')];
    expect(lines.length).toBeGreaterThan(0);
    expect(labels.length).toBeGreaterThan(0);
    for (const line of lines) {
      expect(line.getAttribute('stroke')).toBeNull();
      expect(line.getAttribute('style')).toMatch(/stroke: var\(--soc-alerts-chart-(grid|axis)/);
    }
    for (const label of labels) {
      expect(label.getAttribute('fill')).toBeNull();
      expect(label.getAttribute('style')).toContain('var(--soc-alerts-chart-label');
    }

    fireEvent.click(row.closest('tr') as HTMLTableRowElement);
    fireEvent.click(await screen.findByRole('button', { name: '研判结果' }));
    const stampBand = await waitFor(() => {
      const band = [...container.querySelectorAll('path')].find((path) => path.getAttribute('d') === 'M32 78 H228 V122 H32 Z');
      expect(band).toBeDefined();
      return band as SVGPathElement;
    });
    expect(stampBand.getAttribute('fill')).toBeNull();
    expect(stampBand.getAttribute('style')).toContain('var(--soc-alerts-stamp-band');
    expect(within(container).getAllByText('攻击成功').length).toBeGreaterThan(0);
  });
});
