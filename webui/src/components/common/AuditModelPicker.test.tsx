import React from 'react';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import AuditModelPicker from './AuditModelPicker';
const mock = vi.hoisted(() => ({ hook: vi.fn(), picker: vi.fn() }));
vi.mock('./ChatPromptSelectors', () => ({
  useChatModelOptions: mock.hook,
  ChatModelPicker: (props: any) => { mock.picker(props); return <button onClick={() => props.onSelectModel(props.groupedOptions[0].models[1])}>Select second model</button>; },
}));
const first = { providerID: 'p', modelID: 'one', key: 'p::one' };
const second = { providerID: 'p', modelID: 'two', key: 'p::two' };
beforeEach(() => { mock.hook.mockReturnValue({ options: [first, second], groupedOptions: [{ models: [first, second] }], loading: false, resolvedDefaultModelInitialized: true, primaryModelOption: first }); });
it('resolves a concrete default and excludes Auto from the workbench picker', async () => {
  const onChange = vi.fn(), onReady = vi.fn();
  const view = render(<AuditModelPicker value="" onChange={onChange} onReady={onReady} />);
  await waitFor(() => expect(onChange).toHaveBeenCalledWith('p/one'));
  expect(mock.hook).toHaveBeenCalledWith({ enableAuto: false });
  expect(mock.picker.mock.lastCall![0].autoOption).toBeUndefined();
  view.rerender(<AuditModelPicker value="p/one" onChange={onChange} onReady={onReady} />);
  expect(onReady).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByText('Select second model'));
  expect(onChange).toHaveBeenLastCalledWith('p/two');
});
it('does not accept unavailable saved models or Auto', () => {
  const onReady = vi.fn();
  const view = render(<AuditModelPicker value="auto" onChange={vi.fn()} onReady={onReady} />);
  expect(onReady).toHaveBeenLastCalledWith(false);
  view.rerender(<AuditModelPicker value="removed/model" onChange={vi.fn()} onReady={onReady} />);
  expect(mock.picker.mock.lastCall![0].selectedModelOption).toBeNull();
  expect(onReady).toHaveBeenLastCalledWith(false);
});
