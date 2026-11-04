import { describe, expect, it } from 'vitest';

import { isTextEditingTarget, removeNodesWithEdges } from './graphEditing';

describe('removeNodesWithEdges', () => {
  it('删除节点时连带移除挂在它上面的边', () => {
    const nodes = [{ id: 'a', position: { x: 0, y: 0 }, data: {} }, { id: 'b', position: { x: 0, y: 0 }, data: {} }, { id: 'c', position: { x: 0, y: 0 }, data: {} }];
    const edges = [
      { id: 'a-b', source: 'a', target: 'b' },
      { id: 'b-c', source: 'b', target: 'c' },
      { id: 'a-c', source: 'a', target: 'c' },
    ];

    const result = removeNodesWithEdges(nodes, edges, ['b']);

    expect(result.nodes.map((n) => n.id)).toEqual(['a', 'c']);
    expect(result.edges.map((e) => e.id)).toEqual(['a-c']);
  });
});

describe('isTextEditingTarget', () => {
  it('输入框/文本域/可编辑区域内的按键不触发删除', () => {
    expect(isTextEditingTarget(document.createElement('input'))).toBe(true);
    expect(isTextEditingTarget(document.createElement('textarea'))).toBe(true);
    expect(isTextEditingTarget(document.createElement('select'))).toBe(true);
    const editable = document.createElement('div');
    Object.defineProperty(editable, 'isContentEditable', { value: true });
    expect(isTextEditingTarget(editable)).toBe(true);
  });

  it('画布/按钮上的按键仍走删除逻辑', () => {
    expect(isTextEditingTarget(document.createElement('div'))).toBe(false);
    expect(isTextEditingTarget(document.createElement('button'))).toBe(false);
    expect(isTextEditingTarget(null)).toBe(false);
  });
});
