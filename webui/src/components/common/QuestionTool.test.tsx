import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { QuestionTool } from './QuestionTool';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        'question.multiSelect': '可多选',
        'question.singleSelect': '单选',
        'question.selectedCount': `已选 ${params?.count ?? 0} 项`,
        'question.textPlaceholder': '请输入...',
        'question.needsAnswer': '需要你的回答',
        'question.customAnswer': '自定义 / 补充说明',
        'question.confirm': '确认',
        'question.skip': '跳过',
      };
      return translations[key] ?? key;
    },
  }),
}));

describe('QuestionTool', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('requires standalone text answers before submitting', async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn().mockResolvedValue(undefined);

    render(
      <QuestionTool
        questions={[{
          header: '自定义',
          question: '如果没有补充说明，请留空。',
          type: 'text',
        }]}
        onAnswer={onAnswer}
      />,
    );

    expect(screen.getByRole('button', { name: /确认/ })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /确认/ }));

    expect(onAnswer).not.toHaveBeenCalled();
  });

  it('renders a custom text follow-up inside the preceding choice question', async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn().mockResolvedValue(undefined);

    render(
      <QuestionTool
        questions={[
          {
            header: '输入模式',
            question: '告警将以哪种方式进入 stream_alert_denoise?',
            type: 'choice',
            options: [
              {
                label: 'Syslog 实时流',
                description: '安全设备按 syslog 转发到本机。',
              },
              'API 批次调用',
            ],
          },
          {
            header: 'Step 1 自定义',
            question: '如需自定义/补充说明，请输入；没有则填 none。',
            type: 'text',
          },
        ]}
        onAnswer={onAnswer}
        compact
      />,
    );

    expect(screen.queryByText('Step 1 自定义')).not.toBeInTheDocument();
    expect(screen.getByText('自定义')).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveAttribute('placeholder', 'none');

    await user.click(screen.getByRole('button', { name: /Syslog 实时流/ }));
    await user.click(screen.getByRole('button', { name: /确认/ }));

    expect(onAnswer).toHaveBeenCalledWith([['Syslog 实时流'], ['none']]);
  });

  it('renders choice options that use common non-label fields', async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn().mockResolvedValue(undefined);

    render(
      <QuestionTool
        questions={[{
          header: '数据源',
          question: '漏洞数据源用什么?',
          type: 'choice',
          options: [
            { value: 'NVD', desc: 'Public CVE feed' },
            { text: '内部扫描器' },
            { label: '' },
          ],
        }]}
        onAnswer={onAnswer}
      />,
    );

    expect(screen.getByRole('button', { name: /NVD/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /内部扫描器/ })).toBeInTheDocument();
    expect(screen.queryAllByRole('button')).toHaveLength(3);

    await user.click(screen.getByRole('button', { name: /NVD/ }));
    await user.click(screen.getByRole('button', { name: /确认/ }));

    expect(onAnswer).toHaveBeenCalledWith([['NVD']]);
  });

  it('falls back to text input when a choice question has no visible options', async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn().mockResolvedValue(undefined);

    render(
      <QuestionTool
        questions={[{
          question: '漏洞数据源用什么?',
          type: 'choice',
          options: [{ label: '' }],
        }]}
        onAnswer={onAnswer}
      />,
    );

    await user.type(screen.getByRole('textbox'), 'NVD');
    await user.click(screen.getByRole('button', { name: /确认/ }));

    expect(onAnswer).toHaveBeenCalledWith([['NVD']]);
  });
});
