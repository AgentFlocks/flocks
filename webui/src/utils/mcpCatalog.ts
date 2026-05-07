import type { MCPCatalogEntry } from '@/types';

function isChineseLocale(language?: string): boolean {
  if (!language) return false;
  return language.toLowerCase().replace('_', '-').startsWith('zh');
}

export function getCatalogDescription(
  entry: ({
    description?: string;
    description_cn?: string;
    descriptionCn?: string;
  }) | null | undefined,
  language?: string,
): string {
  if (!entry) return '';
  const englishDescription = entry.description?.trim() || '';
  const chineseDescription = entry.description_cn?.trim() || entry.descriptionCn?.trim() || '';
  return isChineseLocale(language)
    ? (chineseDescription || englishDescription)
    : (englishDescription || chineseDescription);
}

export function getMetadataDescription(metadata: Record<string, unknown> | null | undefined, language?: string): string {
  const englishDescription = typeof metadata?.description === 'string' ? metadata.description.trim() : '';
  const chineseDescription = typeof metadata?.description_cn === 'string' ? metadata.description_cn.trim() : '';
  return isChineseLocale(language)
    ? (chineseDescription || englishDescription)
    : (englishDescription || chineseDescription);
}
