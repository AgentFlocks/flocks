import { describe, expect, it } from 'vitest';
import {
  formatReviewContent, isJsonNode, isMarkdownNode, isRichPreviewNode, isTextPreviewNode,
} from './filePreview';
import type { WorkspaceNode } from '@/api/workspace';

function fileNode(name: string): WorkspaceNode {
  return { name, path: name, type: 'file', is_text_file: true };
}

describe('isMarkdownNode', () => {
  it('returns true for .md and .markdown', () => {
    expect(isMarkdownNode(fileNode('report.md'))).toBe(true);
    expect(isMarkdownNode(fileNode('notes.MD'))).toBe(true);
    expect(isMarkdownNode(fileNode('readme.markdown'))).toBe(true);
  });

  it('returns false for other file types and directories', () => {
    expect(isMarkdownNode(fileNode('data.json'))).toBe(false);
    expect(isMarkdownNode(fileNode('doc.pdf'))).toBe(false);
    expect(isMarkdownNode(fileNode('sheet.xlsx'))).toBe(false);
    expect(isMarkdownNode({ name: 'outputs', path: 'outputs', type: 'directory' })).toBe(false);
  });
});

describe('json and rich preview helpers', () => {
  it('recognizes json files as rich previewable', () => {
    const json = fileNode('result.json');
    expect(isJsonNode(json)).toBe(true);
    expect(isRichPreviewNode(json)).toBe(true);
    expect(isRichPreviewNode(fileNode('report.md'))).toBe(true);
    expect(isRichPreviewNode(fileNode('doc.pdf'))).toBe(false);
  });

  it('returns content unchanged for review rendering', () => {
    const node = fileNode('result.json');
    const content = '{\n  "a": 1\n}';
    expect(formatReviewContent(node, content)).toBe(content);
  });
});

describe('isTextPreviewNode', () => {
  it('uses backend text-file metadata to decide whether content can be read', () => {
    expect(isTextPreviewNode(fileNode('script.py'))).toBe(true);
    expect(isTextPreviewNode({ ...fileNode('image.png'), is_text_file: false })).toBe(false);
    expect(isTextPreviewNode({ name: 'outputs', path: 'outputs', type: 'directory' })).toBe(false);
  });
});
