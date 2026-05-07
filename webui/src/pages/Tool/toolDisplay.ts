import type { Tool, ToolFixture } from '@/api/tool';

export function getLocalizedToolDescription(tool: Pick<Tool, 'description' | 'description_cn'>, language: string): string {
  const normalized = language.toLowerCase().replace('_', '-');
  const englishDescription = tool.description?.trim() || '';
  const chineseDescription = tool.description_cn?.trim() || '';
  if (normalized.startsWith('zh')) {
    return chineseDescription || englishDescription;
  }
  return englishDescription || chineseDescription;
}

/**
 * Pick the fixture label that matches the active UI language.
 * Falls through to the default ``label`` when the locale-specific
 * override is missing — same convention as ``getLocalizedToolDescription``.
 */
export function getLocalizedFixtureLabel(fixture: Pick<ToolFixture, 'label' | 'label_cn'>, language: string): string {
  const normalized = language.toLowerCase().replace('_', '-');
  const cn = fixture.label_cn?.trim() || '';
  const en = fixture.label?.trim() || '';
  if (normalized.startsWith('zh')) {
    return cn || en;
  }
  return en || cn;
}
