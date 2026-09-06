import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import OnboardingModal from './OnboardingModal';
import zhCNCommon from '@/locales/zh-CN/common.json';

const {
  catalogAPI,
  clientPost,
  currentLanguage,
  defaultModelAPI,
  mcpAPI,
  onboardingAPI,
  providerAPI,
  sessionApi,
} = vi.hoisted(() => ({
  catalogAPI: {
    list: vi.fn(),
  },
  clientPost: vi.fn(),
  currentLanguage: {
    value: 'zh-CN',
  },
  defaultModelAPI: {
    getResolved: vi.fn(),
  },
  mcpAPI: {
    revealCredentials: vi.fn(),
  },
  onboardingAPI: {
    getStatus: vi.fn(),
    validate: vi.fn(),
    apply: vi.fn(),
  },
  providerAPI: {
    revealCredentials: vi.fn(),
    revealServiceCredentials: vi.fn(),
  },
  sessionApi: {
    create: vi.fn(),
  },
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

vi.mock('@/api/client', () => ({
  default: {
    post: clientPost,
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: currentLanguage.value },
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

function makeStatus(overrides: Record<string, any> = {}) {
  return {
    completed: false,
    has_default_model: false,
    default_model: null,
    threatbook_intel: {
      configured: false,
      region: null,
      api_configured: false,
      api_service_id: 'threatbook-cn',
      mcp_configured: false,
      mcp_connected: false,
      mcp_status: 'not_configured',
      mcp_name: 'threatbook_mcp',
      service_matrix: {
        cn: ['api', 'mcp'],
        global: ['api', 'mcp'],
      },
    },
    ...overrides,
  };
}

function makeValidation(resourceResults: Record<string, any> = {}) {
  return {
    success: true,
    can_apply: true,
    threatbook_enabled: true,
    threatbook_key_valid: true,
    threatbook_region_match: true,
    suggested_region: null,
    error_code: null,
    message: 'ok',
    threatbook_resources: Object.keys(resourceResults),
    third_party_llm_valid: null,
    resource_results: resourceResults,
  };
}

function renderOnboarding() {
  return render(
    <MemoryRouter>
      <OnboardingModal onClose={vi.fn()} />
    </MemoryRouter>,
  );
}

describe('OnboardingModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentLanguage.value = 'zh-CN';
    defaultModelAPI.getResolved.mockRejectedValue(new Error('no default model'));
    providerAPI.revealCredentials.mockResolvedValue({
      data: { has_credential: true, api_key: 'saved-model-key' },
    });
    providerAPI.revealServiceCredentials.mockResolvedValue({
      data: { has_credential: true, api_key: 'saved-intel-key' },
    });
    mcpAPI.revealCredentials.mockResolvedValue({
      data: { api_key: 'saved-intel-mcp-key' },
    });
    onboardingAPI.getStatus.mockResolvedValue({
      data: makeStatus(),
    });
    catalogAPI.list.mockResolvedValue({
      data: {
        providers: [
          makeProvider('threatbook-cn-llm', 'ThreatBook CN', [
            { id: 'minimax-m2.7', name: 'MiniMax M2.7' },
          ]),
          makeProvider('threatbook-io-llm', 'ThreatBook Global', [
            { id: 'minimax-m2.7', name: 'MiniMax M2.7' },
          ]),
          makeProvider('openai-compatible', 'OpenAI Compatible', []),
          makeProvider('deepseek', 'DeepSeek', [{ id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash' }]),
        ],
      },
    });
    onboardingAPI.validate.mockResolvedValue({
      data: makeValidation({
        threatbook_llm: {
          enabled: true,
          success: true,
          code: null,
          message: 'model ok',
          details: {},
        },
      }),
    });
    onboardingAPI.apply.mockResolvedValue({
      data: {
        success: true,
        message: 'saved',
        region: 'cn',
        threatbook_enabled: true,
        configured: ['threatbook_llm', 'default_llm'],
        skipped: [],
        default_model: {
          provider_id: 'threatbook-cn-llm',
          model_id: 'minimax-m2.7',
        },
      },
    });
    sessionApi.create.mockResolvedValue({ id: 'session-1' });
    clientPost.mockResolvedValue({ data: {} });
  });

  it('merges China and Global ThreatBook providers into one free model option', async () => {
    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });

    expect(screen.getByRole('option', { name: 'onboarding.bootstrap.providerThreatBookFree' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'ThreatBook CN' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'ThreatBook Global' })).not.toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'OpenAI Compatible' })).toBeInTheDocument();

    const keyLink = screen.getByRole('link', { name: 'onboarding.bootstrap.modelKeyLink' });
    expect(keyLink).toHaveAttribute('href', 'https://portal.agentflocks.com');
    expect(screen.queryByText('onboarding.bootstrap.modelRegionTitle')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.regionChina' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.regionGlobal' })).not.toBeInTheDocument();
  });

  it('saves ThreatBook model setup without applying intelligence API or MCP services', async () => {
    const user = userEvent.setup();

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder'), 'tb-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' }));

    await waitFor(() => {
      expect(onboardingAPI.validate).toHaveBeenCalledWith(expect.objectContaining({
        region: 'cn',
        use_threatbook_model: true,
        threatbook_api_key: 'tb-key',
        threatbook_model_only: true,
      }));
    });
    expect(onboardingAPI.apply).toHaveBeenCalledWith(expect.objectContaining({
      threatbook_model_only: true,
    }));
    expect(screen.queryByText('onboarding.bootstrap.resourceLabels.threatbook_api')).not.toBeInTheDocument();
    expect(screen.queryByText('onboarding.bootstrap.resourceLabels.threatbook_mcp')).not.toBeInTheDocument();
  });

  it('automatically retries the suggested region for the merged ThreatBook model option', async () => {
    const user = userEvent.setup();

    onboardingAPI.validate
      .mockResolvedValueOnce({
        data: {
          ...makeValidation({
            threatbook_llm: {
              enabled: true,
              success: false,
              code: 'region_mismatch',
              message: 'wrong region',
              details: {},
            },
          }),
          success: false,
          can_apply: false,
          threatbook_region_match: false,
          suggested_region: 'global',
          error_code: 'region_mismatch',
          message: 'wrong region',
        },
      })
      .mockResolvedValueOnce({
        data: makeValidation({
          threatbook_llm: {
            enabled: true,
            success: true,
            code: null,
            message: 'model ok',
            details: {},
          },
        }),
      });
    onboardingAPI.apply.mockResolvedValueOnce({
      data: {
        success: true,
        message: 'saved',
        region: 'global',
        threatbook_enabled: true,
        configured: ['threatbook_llm', 'default_llm'],
        skipped: [],
        default_model: {
          provider_id: 'threatbook-io-llm',
          model_id: 'minimax-m2.7',
        },
      },
    });

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder'), 'tb-global-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' }));

    await waitFor(() => {
      expect(onboardingAPI.validate).toHaveBeenCalledTimes(2);
    });
    expect(onboardingAPI.validate).toHaveBeenNthCalledWith(1, expect.objectContaining({
      region: 'cn',
      use_threatbook_model: true,
      threatbook_api_key: 'tb-global-key',
      threatbook_model_only: true,
    }));
    expect(onboardingAPI.validate).toHaveBeenNthCalledWith(2, expect.objectContaining({
      region: 'global',
      use_threatbook_model: true,
      threatbook_api_key: 'tb-global-key',
      threatbook_model_only: true,
    }));
    expect(onboardingAPI.apply).toHaveBeenCalledWith(expect.objectContaining({
      region: 'global',
      threatbook_model_only: true,
    }));
    expect(screen.queryByText('onboarding.bootstrap.switchToGlobal')).not.toBeInTheDocument();
  });

  it('shows the model skip confirmation and advances to intelligence setup', async () => {
    const user = userEvent.setup();

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.skipPage' }));

    expect(screen.getByText('onboarding.bootstrap.skipModelDescription')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.confirmSkip' }));

    expect(screen.getByText('onboarding.bootstrap.intelPageTitle')).toBeInTheDocument();
  });

  it('uses neutral skip descriptions that point users to later setup locations', () => {
    const bootstrap = zhCNCommon.onboarding.bootstrap;

    expect(bootstrap.skipModelDescription).toBe('可以先跳过此步骤，稍后在左侧导航栏「模型清单」中继续完成默认模型配置。');
    expect(bootstrap.skipIntelDescription).toBe('可以先跳过此步骤，稍后在左侧导航栏「工具清单」页面的「MCP」页签中，找到 ThreatBook MCP 后继续完成微步情报 API 与 MCP 配置。');
    expect(bootstrap.skipModelDescription).not.toMatch(/无法|不可用|不能|失败/);
    expect(bootstrap.skipIntelDescription).not.toMatch(/无法|不可用|不能|失败/);
  });

  it('shows domestic and global intelligence activation links by selected region', async () => {
    const user = userEvent.setup();

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));

    const domesticLink = screen.getByRole('link', { name: 'onboarding.bootstrap.intelKeyLink' });
    expect(domesticLink).toHaveAttribute('href', 'https://x.threatbook.com/flocks/activate');
    expect(screen.getByText('onboarding.bootstrap.intelMcpCapability')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.intelRegionGlobal' }));

    const globalLink = screen.getByRole('link', { name: 'onboarding.bootstrap.intelKeyLink' });
    expect(globalLink).toHaveAttribute('href', 'https://i.threatbook.io/flocks/activate');
    expect(screen.getByText('onboarding.bootstrap.intelMcpCapability')).toBeInTheDocument();
  });

  it('defaults intelligence setup to the international region in English', async () => {
    const user = userEvent.setup();
    currentLanguage.value = 'en-US';

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));

    const keyLink = screen.getByRole('link', { name: 'onboarding.bootstrap.intelKeyLink' });
    expect(keyLink).toHaveAttribute('href', 'https://i.threatbook.io/flocks/activate');
    expect(screen.getByText('onboarding.bootstrap.intelMcpCapability')).toBeInTheDocument();
  });

  it('shows configured keys masked in edit mode and reveals them on demand', async () => {
    const user = userEvent.setup();

    onboardingAPI.getStatus.mockResolvedValue({
      data: makeStatus({
        completed: true,
        has_default_model: true,
        default_model: {
          provider_id: 'threatbook-cn-llm',
          model_id: 'minimax-m2.7',
        },
        threatbook_intel: {
          configured: true,
          region: 'cn',
          api_configured: true,
          api_service_id: 'threatbook-cn',
          mcp_configured: true,
          mcp_connected: true,
          mcp_status: 'connected',
          mcp_name: 'threatbook_mcp',
          service_matrix: {
            cn: ['api', 'mcp'],
            global: ['api', 'mcp'],
          },
        },
      }),
    });

    renderOnboarding();

    await screen.findByText('onboarding.bootstrap.primaryConfiguredSummary');
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.editPrimary' }));
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).toBeInTheDocument();
    const modelKeyInput = screen.getByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder');
    expect(modelKeyInput).toHaveValue('************');
    expect(modelKeyInput).toHaveAttribute('type', 'password');
    expect(modelKeyInput).toHaveAttribute('readonly');
    expect(providerAPI.revealCredentials).not.toHaveBeenCalled();

    await user.click(screen.getByTitle('onboarding.bootstrap.showKey'));
    await waitFor(() => {
      expect(providerAPI.revealCredentials).toHaveBeenCalledWith('threatbook-cn-llm');
      expect(modelKeyInput).toHaveValue('saved-model-key');
    });
    expect(modelKeyInput).toHaveAttribute('type', 'text');

    await user.click(screen.getByTitle('onboarding.bootstrap.hideKey'));
    expect(modelKeyInput).toHaveValue('************');
    expect(modelKeyInput).toHaveAttribute('type', 'password');

    const providerSelect = screen.getByRole('combobox');
    await user.selectOptions(providerSelect, 'openai-compatible');
    expect(modelKeyInput).toHaveValue('');
    expect(modelKeyInput).not.toHaveAttribute('readonly');
    await user.selectOptions(providerSelect, 'threatbook-free');
    expect(modelKeyInput).toHaveValue('************');
    expect(modelKeyInput).toHaveAttribute('readonly');

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));
    expect(screen.getByText('onboarding.bootstrap.intelConfiguredHint')).toBeInTheDocument();
    expect(screen.getAllByText('onboarding.bootstrap.intelConfiguredVerified').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.editIntel' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.editIntel' }));
    const intelKeyInput = screen.getByPlaceholderText('onboarding.bootstrap.intelKeyPlaceholder');
    expect(intelKeyInput).toHaveValue('************');
    expect(intelKeyInput).toHaveAttribute('type', 'password');
    expect(intelKeyInput).toHaveAttribute('readonly');
    expect(providerAPI.revealServiceCredentials).not.toHaveBeenCalled();

    await user.click(screen.getByTitle('onboarding.bootstrap.showKey'));
    await waitFor(() => {
      expect(providerAPI.revealServiceCredentials).toHaveBeenCalledWith('threatbook-cn');
      expect(intelKeyInput).toHaveValue('saved-intel-key');
    });
    expect(intelKeyInput).toHaveAttribute('type', 'text');

    await user.click(screen.getByTitle('onboarding.bootstrap.hideKey'));
    expect(intelKeyInput).toHaveValue('************');
    expect(intelKeyInput).toHaveAttribute('type', 'password');

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.intelRegionGlobal' }));
    expect(intelKeyInput).toHaveValue('');
    expect(intelKeyInput).not.toHaveAttribute('readonly');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.intelRegionChina' }));
    expect(intelKeyInput).toHaveValue('************');
    expect(intelKeyInput).toHaveAttribute('readonly');
  });

  it('starts Rex onboarding after skipping intelligence setup', async () => {
    const user = userEvent.setup();

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.skipPage' }));
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.confirmSkip' }));

    await waitFor(() => {
      expect(sessionApi.create).toHaveBeenCalledWith({ title: 'onboarding.sessionTitle' });
    });
    expect(clientPost).toHaveBeenCalledWith('/api/session/session-1/prompt_async', {
      parts: [{ type: 'text', text: 'onboarding.initialMessage' }],
    });
  });
});
