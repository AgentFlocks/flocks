import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import EntitySheet from '@/components/common/EntitySheet';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@/hooks/useSessionChat', () => ({
  useSessionChat: vi.fn(() => ({
    sessionId: null,
    loading: false,
    error: null,
    create: vi.fn().mockResolvedValue(undefined),
    retry: vi.fn().mockResolvedValue(undefined),
    reset: vi.fn(),
  })),
}));

// Provide a t() that returns actual Chinese text so the test assertions match
// what users see in the browser (mirror of zh-CN/common.json entity section).
const entityTranslations: Record<string, string> = {
  'entity.createTitle': '创建 {{entityType}}',
  'entity.editTitle': '编辑 {{entityType}}',
  'entity.editTitleWithName': '编辑 {{entityType}}：{{entityName}}',
  'entity.defaultCreate': '创建',
  'entity.defaultSave': '保存',
  'entity.tabDetails': '详情',
  'entity.tabAIEdit': '工作台',
  'entity.tabTest': '测试',
  'entity.rexThinking': 'Agent 正在思考中...',
  'entity.editAndSend': '编辑下方内容，发送给 Agent 查看效果',
  'entity.reTest': '重新测试',
  'entity.testInputPlaceholder': '输入测试内容',
  'entity.startingRex': '正在启动 Rex 对话...',
  'entity.rexRetry': '重试',
  'entity.rexInputPlaceholder': '描述你想要的配置...',
  'entity.rexReady': 'Rex 准备就绪，请描述你的需求...',
  'entity.testButton': '测试',
  'entity.cancelButton': '取消',
  'entity.extracting': '提取中...',
  'entity.extractFromRex': '从 Rex 提取配置',
  'entity.switchToForm': '切换到表单',
  'entity.testStartFailed': '测试启动失败',
  'entity.extractFailed': '提取失败，请重试',
  'entity.rexAssist': 'Rex 协助',
};

function fakeT(key: string, opts?: Record<string, string>): string {
  let val = entityTranslations[key] ?? key;
  if (opts) {
    Object.entries(opts).forEach(([k, v]) => {
      val = val.replace(`{{${k}}}`, v);
    });
  }
  return val;
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: fakeT, i18n: { changeLanguage: vi.fn() } }),
  Trans: ({ children }: { children: React.ReactNode }) => children,
  initReactI18next: { type: '3rdParty', init: vi.fn() },
}));

vi.mock('@/api/client', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  apiClient: {
    get: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  getApiBase: () => '',
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('EntitySheet', () => {
  const defaultProps = {
    open: true,
    mode: 'create' as const,
    entityType: 'Agent',
    rexSystemContext: 'You are an agent creator.',
    rexWelcomeMessage: 'Tell me about the agent you want to create.',
    onClose: vi.fn(),
    onSubmit: vi.fn(),
    // Start on form tab so the form footer (submit/cancel buttons) is visible
    initialTab: 'form' as const,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Tab navigation', () => {
    it('should default to Rex tab in create mode', () => {
      render(
        <EntitySheet {...defaultProps}>
          <div>Form content</div>
        </EntitySheet>,
      );

      expect(screen.getByText('工作台')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '详情' })).toHaveClass('flex-1');
      expect(screen.getByRole('button', { name: '工作台' })).toHaveClass('flex-1');
    });

    it('should default to Form tab in edit mode', () => {
      render(
        <EntitySheet {...defaultProps} mode="edit" entityName="test-agent">
          <div>Form content</div>
        </EntitySheet>,
      );

      expect(screen.getByText('详情')).toBeInTheDocument();
    });

    it('should switch to Form tab when clicked', async () => {
      const user = userEvent.setup();
      render(
        <EntitySheet {...defaultProps}>
          <div>Form content</div>
        </EntitySheet>,
      );

      const formTab = screen.getByText('详情');
      await user.click(formTab);

      expect(screen.getByText('Form content')).toBeInTheDocument();
    });
  });

  describe('Header', () => {
    it('should show "创建 Agent" in create mode', () => {
      render(
        <EntitySheet {...defaultProps}>
          <div>Form content</div>
        </EntitySheet>,
      );

      expect(screen.getByText('创建 Agent')).toBeInTheDocument();
    });

    it('should show "编辑 Agent：test-agent" in edit mode', () => {
      render(
        <EntitySheet {...defaultProps} mode="edit" entityName="test-agent">
          <div>Form content</div>
        </EntitySheet>,
      );

      expect(screen.getByText('编辑 Agent：test-agent')).toBeInTheDocument();
    });
  });

  describe('Footer actions', () => {
    it('should show "创建" button in create mode', () => {
      render(
        <EntitySheet {...defaultProps}>
          <div>Form content</div>
        </EntitySheet>,
      );

      expect(screen.getByText('创建')).toBeInTheDocument();
    });

    it('should show "保存" button in edit mode', () => {
      render(
        <EntitySheet {...defaultProps} mode="edit">
          <div>Form content</div>
        </EntitySheet>,
      );

      expect(screen.getByText('保存')).toBeInTheDocument();
    });

    it('should call onSubmit when button clicked', async () => {
      const user = userEvent.setup();
      const onSubmit = vi.fn().mockResolvedValue(undefined);

      render(
        <EntitySheet {...defaultProps} onSubmit={onSubmit}>
          <div>Form content</div>
        </EntitySheet>,
      );

      const submitButton = screen.getByText('创建');
      await user.click(submitButton);

      expect(onSubmit).toHaveBeenCalled();
    });

    it('should call onClose when cancel clicked', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();

      render(
        <EntitySheet {...defaultProps} onClose={onClose}>
          <div>Form content</div>
        </EntitySheet>,
      );

      const cancelButton = screen.getByText('取消');
      await user.click(cancelButton);

      expect(onClose).toHaveBeenCalled();
    });
  });

  describe('Close button', () => {
    it('should call onClose when X button clicked', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();

      render(
        <EntitySheet {...defaultProps} onClose={onClose}>
          <div>Form content</div>
        </EntitySheet>,
      );

      const closeButtons = document.querySelectorAll('button');
      const xButton = closeButtons[0];
      await user.click(xButton);

      expect(onClose).toHaveBeenCalled();
    });
  });

  describe('Test tab', () => {
    it('should show test tab when onRunTest is provided', () => {
      render(
        <EntitySheet
          {...defaultProps}
          onRunTest={async () => 'test-session-id'}
        >
          <div>Form content</div>
        </EntitySheet>,
      );

      const testTabs = screen.getAllByRole('button', { name: /测试/ });
      expect(testTabs.length).toBeGreaterThan(0);
    });

    it('should not show test tab when onRunTest is not provided', () => {
      render(
        <EntitySheet {...defaultProps}>
          <div>Form content</div>
        </EntitySheet>,
      );

      const testTabs = screen.queryAllByRole('button', { name: /测试/ });
      expect(testTabs.length).toBe(0);
    });
  });

  describe('Loading state', () => {
    it('should show custom submit label when provided', () => {
      render(
        <EntitySheet {...defaultProps} submitLabel="自定义提交">
          <div>Form content</div>
        </EntitySheet>,
      );

      expect(screen.getByText('自定义提交')).toBeInTheDocument();
    });

    it('should show loading state when submitLoading is true', () => {
      render(
        <EntitySheet {...defaultProps} submitLoading>
          <div>Form content</div>
        </EntitySheet>,
      );

      const submitButton = screen.getByText('创建');
      expect(submitButton).toBeDisabled();
    });
  });

  describe('Disabled state', () => {
    it('should disable submit button when submitDisabled is true', () => {
      render(
        <EntitySheet {...defaultProps} submitDisabled>
          <div>Form content</div>
        </EntitySheet>,
      );

      const submitButton = screen.getByText('创建');
      expect(submitButton).toBeDisabled();
    });
  });
});
