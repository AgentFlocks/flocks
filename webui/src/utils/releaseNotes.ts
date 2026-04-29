const CHINESE_SECTION_ALIASES = new Set(['中文', '简体中文', 'zh-cn', 'zh_cn', 'chinese']);
const ENGLISH_SECTION_ALIASES = new Set(['english', 'en-us', 'en_us']);

interface ReleaseNoteSection {
  title: string;
  body: string;
}

const normalizeSectionTitle = (title: string) => (
  title
    .trim()
    .replace(/^#+\s*/, '')
    .replace(/\s*#+$/, '')
    .toLowerCase()
);

const getSectionLanguage = (title: string): 'zh' | 'en' | null => {
  const normalized = normalizeSectionTitle(title);
  if (CHINESE_SECTION_ALIASES.has(normalized)) return 'zh';
  if (ENGLISH_SECTION_ALIASES.has(normalized)) return 'en';
  return null;
};

const parseReleaseNoteSections = (notes: string): ReleaseNoteSection[] => {
  const lines = notes.split(/\r?\n/);
  const sections: ReleaseNoteSection[] = [];
  let currentTitle: string | null = null;
  let currentBody: string[] = [];

  const flush = () => {
    if (currentTitle === null) return;
    sections.push({
      title: currentTitle,
      body: currentBody.join('\n').trim(),
    });
  };

  for (const line of lines) {
    const heading = line.match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/);
    if (heading && getSectionLanguage(heading[1]) !== null) {
      flush();
      currentTitle = heading[1];
      currentBody = [];
      continue;
    }

    if (currentTitle !== null) {
      currentBody.push(line);
    }
  }

  flush();
  return sections;
};

export const getLocalizedReleaseNotes = (
  notes: string | null | undefined,
  language: string | null | undefined,
): string => {
  const fallback = notes?.trim() ?? '';
  if (!fallback) return '';

  const targetLanguage = (language ?? '').toLowerCase().startsWith('zh') ? 'zh' : 'en';
  const sections = parseReleaseNoteSections(fallback);
  const matched = sections.find((section) => getSectionLanguage(section.title) === targetLanguage);

  return matched?.body || fallback;
};
