const CHINESE_SECTION_ALIASES = new Set(['中文', '简体中文', 'zh-cn', 'zh_cn', 'chinese']);
const ENGLISH_SECTION_ALIASES = new Set(['english', 'en-us', 'en_us']);

interface DetailsSection {
  language: 'zh' | 'en' | null;
  body: string;
  fullBlock: string;
}

const normalizeSectionTitle = (title: string) => (
  title
    .trim()
    .replace(/^#+\s*/, '')
    .replace(/\s*#+$/, '')
    .replace(/<[^>]+>/g, '')
    .toLowerCase()
);

const getSectionLanguage = (title: string): 'zh' | 'en' | null => {
  const normalized = normalizeSectionTitle(title);
  if (CHINESE_SECTION_ALIASES.has(normalized)) return 'zh';
  if (ENGLISH_SECTION_ALIASES.has(normalized)) return 'en';
  return null;
};

const parseDetailsSections = (notes: string): DetailsSection[] => {
  const sections: DetailsSection[] = [];
  const detailsPattern = /<details\b[^>]*>([\s\S]*?)<\/details>/gi;
  let match: RegExpExecArray | null;

  while ((match = detailsPattern.exec(notes)) !== null) {
    const fullBlock = match[0];
    const inner = match[1];
    const summary = inner.match(/<summary\b[^>]*>([\s\S]*?)<\/summary>/i);
    if (!summary) continue;

    sections.push({
      language: getSectionLanguage(summary[1]),
      body: inner.replace(summary[0], '').trim(),
      fullBlock,
    });
  }

  return sections;
};

const removeDetailsBlocks = (notes: string, sections: DetailsSection[]) => (
  sections
    .reduce((value, section) => value.replace(section.fullBlock, ''), notes)
    .replace(/\n{3,}/g, '\n\n')
    .trim()
);

export const getLocalizedReleaseNotes = (
  notes: string | null | undefined,
  language: string | null | undefined,
): string => {
  const fallback = notes?.trim() ?? '';
  if (!fallback) return '';

  const targetLanguage = (language ?? '').toLowerCase().startsWith('zh') ? 'zh' : 'en';
  const detailsSections = parseDetailsSections(fallback);
  const matchedDetails = detailsSections.find((section) => section.language === targetLanguage);
  if (matchedDetails?.body) return matchedDetails.body;

  if (targetLanguage === 'en' && detailsSections.some((section) => section.language === 'zh')) {
    return removeDetailsBlocks(fallback, detailsSections);
  }

  return fallback;
};
