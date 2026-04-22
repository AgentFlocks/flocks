import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Layout from './Layout';
import Home from '@/pages/Home';

const {
  catalogAPI,
  checkUpdate,
  defaultModelAPI,
  mcpAPI,
  onboardingAPI,
  providerAPI,
  sessionApi,
  useStats,
} = vi.hoisted(() => ({
  catalogAPI: {
    list: vi.fn(),
  },
  checkUpdate: vi.fn(),
  defaultModelAPI: {
    getResolved: vi.fn(),
  },
  mcpAPI: {
    getCredentials: vi.fn(),
  },
  onboardingAPI: {
    validate: vi.fn(),
    apply: vi.fn(),
  },
  providerAPI: {
    getServiceCredentials: vi.fn(),
  },
  sessionApi: {
    create: vi.fn(),
  },
  useStats: vi.fn(),
}));

vi.mock('@/api/provider', () => ({
  catalogAPI,
  defaultModelAPI,
  providerAPI,
}));

vi.mock('@/api/mcp', () => ({
  mcpAPI,
}));

vi.mock('@/api/onboarding', () => ({
  onboardingAPI,
}));

vi.mock('@/api/session', () => ({
  sessionApi,
}));

vi.mock('@/api/update', () => ({
  checkUpdate,
}));

vi.mock('@/hooks/useStats', () => ({
  useStats,
}));

vi.mock('@/components/common/LanguageSwitcher', () => ({
  default: () => null,
}));

vi.mock('@/components/common/UpdateModal', () => ({
  UPDATE_DISMISSED_KEY: 'update-dismissed',
  default: () => null,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN', changeLanguage: vi.fn() },
  }),
}));

function makeProvider(id: string, name: string, models: Array<{ id: string; name: string }>) {
  return {
    id,
    name,
    description: null,
    credential_schemas: [
      {
        auth_method: 'api_key',
        fields: [
          {
            name: 'api_key',
            label: 'API Key',
            type: 'secret' as const,
            required: true,
            placeholder: '',
          },
        ],
      },
    ],
    env_vars: [],
    default_base_url: null,
    model_count: models.length,
    models: models.map((model) => ({
      ...model,
      model_type: 'llm',
      status: 'active',
      capabilities: {
        supports_tools: true,
        supports_vision: false,
        supports_reasoning: true,
        supports_streaming: true,
      },
    })),
  };
}

function renderHomeWithLayout() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Home />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

async function flushEffects() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

describe('Layout onboarding entry', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    localStorage.clear();

    checkUpdate.mockResolvedValue({
      has_update: false,
      latest_version: null,
      current_version: '0.2.0',
      error: null,
    });

    useStats.mockReturnValue({
      stats: {
        agents: { total: 0 },
        workflows: { total: 0 },
        skills: { total: 0 },
        tools: { total: 0 },
        tasks: { week: 0, scheduledActive: 0 },
        models: { total: 0 },
        system: { status: 'healthy' },
      },
      loading: false,
      error: null,
    });

    defaultModelAPI.getResolved.mockResolvedValue({
      data: {
        provider_id: 'threatbook-cn-llm',
        model_id: 'minimax-m2.7',
      },
    });

    catalogAPI.list.mockResolvedValue({
      data: {
        providers: [
          makeProvider('threatbook-cn-llm', 'ThreatBook CN', [
            { id: 'minimax-m2.7', name: 'MiniMax M2.7' },
            { id: 'qwen3.6-plus', name: 'Qwen3.6 Plus' },
            { id: 'qwen3-max', name: 'Qwen 3 Max' },
          ]),
          makeProvider('threatbook-io-llm', 'ThreatBook Global', [
            { id: 'minimax-m2.7', name: 'MiniMax M2.7' },
            { id: 'qwen3.6-plus', name: 'Qwen3.6 Plus' },
            { id: 'qwen3-max', name: 'Qwen 3 Max' },
          ]),
          makeProvider('openai-compatible', 'OpenAI Compatible', []),
          makeProvider('deepseek', 'DeepSeek', [{ id: 'deepseek-chat', name: 'DeepSeek V3.2' }]),
        ],
      },
    });

    providerAPI.getServiceCredentials.mockResolvedValue({
      data: { has_credential: false },
    });

    mcpAPI.getCredentials.mockResolvedValue({
      data: { has_credential: false },
    });

    onboardingAPI.apply.mockResolvedValue({
      data: { success: true },
    });

    sessionApi.create.mockResolvedValue({ id: 'session-1' });
  });

  it('opens onboarding from the home entry and shows configured details for an existing default model', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');

    renderHomeWithLayout();

    await user.click(screen.getByRole('button', { name: 'getStarted' }));

    await screen.findByText('onboarding.bootstrap.primaryConfiguredSummary');

    await user.click(screen.getByText('onboarding.bootstrap.primaryTitle'));

    expect(screen.getByText('onboarding.bootstrap.configuredDetailsTitle')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.editPrimary' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('onboarding.bootstrap.tbPlaceholder')).not.toBeInTheDocument();
  });

  it('polls update checks hourly', async () => {
    vi.useFakeTimers();

    renderHomeWithLayout();

    await flushEffects();
    expect(checkUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_599_999);
    });
    expect(checkUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(checkUpdate).toHaveBeenCalledTimes(2);
  });

  it('enforces a one-minute minimum gap for focus-triggered update checks', async () => {
    vi.useFakeTimers();

    renderHomeWithLayout();

    await flushEffects();
    expect(checkUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(59_000);
    });
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await flushEffects();
    expect(checkUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await flushEffects();
    expect(checkUpdate).toHaveBeenCalledTimes(2);
  });
});
