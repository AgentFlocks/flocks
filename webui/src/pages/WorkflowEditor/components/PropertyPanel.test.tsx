import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import PropertyPanel from './PropertyPanel';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI: { list: vi.fn().mockResolvedValue({ data: [] }) },
}));

function toolNode(toolArgs: Record<string, unknown> | undefined) {
  return {
    id: 't',
    type: 'tool',
    position: { x: 0, y: 0 },
    data: { label: 't', tool_name: 'x', tool_args: toolArgs },
  };
}

describe('PropertyPanel JSON fields', () => {
  it('打开 tool 节点时静态参数回显已保存的 tool_args', () => {
    render(
      <PropertyPanel
        selectedNode={toolNode({ a: 1 })}
        currentWorkflowId="wf-1"
        onClose={vi.fn()}
        onUpdate={vi.fn()}
      />,
    );

    const textarea = screen.getByPlaceholderText('{ "param": "value" }') as HTMLTextAreaElement;
    expect(JSON.parse(textarea.value)).toEqual({ a: 1 });
  });

  it('非法 JSON 失焦后提示格式错误且不写回节点数据', () => {
    const onUpdate = vi.fn();
    render(
      <PropertyPanel
        selectedNode={toolNode({ a: 1 })}
        currentWorkflowId="wf-1"
        onClose={vi.fn()}
        onUpdate={onUpdate}
      />,
    );

    const textarea = screen.getByPlaceholderText('{ "param": "value" }') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: '{bad' } });
    fireEvent.blur(textarea);
    expect(screen.getByText('editor.jsonFormatError')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'editor.applyChanges' }));
    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(onUpdate.mock.calls[0][1].tool_args).toEqual({ a: 1 });
  });
});
