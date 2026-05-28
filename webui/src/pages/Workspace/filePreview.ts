import type { WorkspaceNode } from '@/api/workspace';

/** True when the node is a Markdown file eligible for rendered preview. */
export function isMarkdownNode(node: WorkspaceNode): boolean {
  if (node.type !== 'file') return false;
  const ext = node.name.split('.').pop()?.toLowerCase() ?? '';
  return ext === 'md' || ext === 'markdown';
}

/** True when the node is a JSON file eligible for code-block preview. */
export function isJsonNode(node: WorkspaceNode): boolean {
  if (node.type !== 'file') return false;
  const ext = node.name.split('.').pop()?.toLowerCase() ?? '';
  return ext === 'json';
}

/** Rich preview currently supports Markdown and JSON. */
export function isRichPreviewNode(node: WorkspaceNode): boolean {
  return isMarkdownNode(node) || isJsonNode(node);
}

/** True when the node can be read and edited as text. */
export function isTextPreviewNode(node: WorkspaceNode): boolean {
  return node.type === 'file' && Boolean(node.is_text_file);
}

/** Build content for Review tab rendering. */
export function formatReviewContent(node: WorkspaceNode, content: string): string {
  return content;
}
