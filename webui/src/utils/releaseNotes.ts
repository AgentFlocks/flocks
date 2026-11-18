const CHINESE_SECTION_ALIASES = new Set([
  '中文',
  '简体中文',
  'zh-cn',
  'zh_cn',
  'chinese',
  'chinese release notes',
]);
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

/** Readers' names for the commit scopes release notes are written with. */
const SCOPE_LABELS: Record<'zh' | 'en', Record<string, string>> = {
  zh: {
    models: '模型管理', model: '模型管理', session: '会话', webui: '界面', ui: '界面', workflow: '工作流',
    enterprise: '企业版', runtime: '运行环境', agent: '智能体', agents: '智能体', tool: '工具', tools: '工具',
    mcp: 'MCP', skill: '技能', skills: '技能', channel: '通道', channels: '通道', task: '任务', tasks: '任务',
    device: '设备', devices: '设备', hub: '场景套件', auth: '账号', security: '安全', updater: '升级', update: '升级',
  },
  en: {
    models: 'Models', model: 'Models', session: 'Sessions', webui: 'Interface', ui: 'Interface', workflow: 'Workflows',
    enterprise: 'Enterprise edition', runtime: 'Runtime', agent: 'Agents', agents: 'Agents', tool: 'Tools', tools: 'Tools',
    mcp: 'MCP', skill: 'Skills', skills: 'Skills', channel: 'Channels', channels: 'Channels', task: 'Tasks', tasks: 'Tasks',
    device: 'Devices', devices: 'Devices', hub: 'Scene suites', auth: 'Accounts', security: 'Security', updater: 'Updates', update: 'Updates',
  },
};

const COMMIT_PREFIX = /^(\s*[-*]\s+)(?:feat|fix|perf|refactor|chore|docs|build|ci|style|test)(?:\(([^)]+)\))?!?\s*[:：]\s*(.+)$/i;

/**
 * Release notes are written as commit subjects ("feat(models)：简化模型发现…").
 * Readers get the area by name instead of the commit type and code module
 * ("模型管理：简化模型发现…"); lines written any other way are left alone.
 */
export const humanizeReleaseNotes = (notes: string, language: 'zh' | 'en'): string => {
  // Lines inside fenced code blocks are examples, shown as written. A fence closes only
  // on the same character repeated at least as many times (CommonMark), so a ``` line
  // inside a ```` block stays part of the example.
  let fence: { char: string; length: number } | null = null;
  return notes
    .split('\n')
    .map((line) => {
      const marker = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
      if (marker) {
        const [, run, rest] = marker;
        if (!fence) {
          fence = { char: run[0], length: run.length };
          return line;
        }
        if (run[0] === fence.char && run.length >= fence.length && !rest.trim()) {
          fence = null;
          return line;
        }
      }
      const match = fence ? null : line.match(COMMIT_PREFIX);
      if (!match) return line;
      const [, bullet, scope, text] = match;
      const label = scope ? SCOPE_LABELS[language][scope.trim().toLowerCase()] : undefined;
      const body = language === 'zh' && !/[。！？.!?]$/.test(text.trim()) ? `${text.trim()}。` : text.trim();
      return `${bullet}${label ? `${label}${language === 'zh' ? '：' : ': '}` : ''}${body}`;
    })
    .join('\n');
};

const selectLocalizedReleaseNotes = (fallback: string, targetLanguage: 'zh' | 'en'): string => {
  const detailsSections = parseDetailsSections(fallback);
  const matchedDetails = detailsSections.find((section) => section.language === targetLanguage);
  if (matchedDetails?.body) return matchedDetails.body;

  if (targetLanguage === 'en' && detailsSections.some((section) => section.language === 'zh')) {
    return removeDetailsBlocks(fallback, detailsSections);
  }

  return fallback;
};

export const getLocalizedReleaseNotes = (
  notes: string | null | undefined,
  language: string | null | undefined,
): string => {
  const fallback = notes?.trim() ?? '';
  if (!fallback) return '';

  const targetLanguage = (language ?? '').toLowerCase().startsWith('zh') ? 'zh' : 'en';
  return humanizeReleaseNotes(selectLocalizedReleaseNotes(fallback, targetLanguage), targetLanguage);
};
