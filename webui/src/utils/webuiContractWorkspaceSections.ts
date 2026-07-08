import type {
  WebUIContractPageListItem,
  WebUIContractWorkspaceListItem,
  WebUIContractWorkspaceSection,
} from '@/api/webuiContractPages';

export type WebUIContractWorkspaceContentPadding = 'comfortable' | 'none';
export type WebUIContractWorkspaceThemeOverride = 'light' | 'dark';

export interface WebUIContractWorkspaceSectionView {
  id: string;
  label: string;
  pages: WebUIContractPageListItem[];
  defaultPageId: string;
  contentPadding: WebUIContractWorkspaceContentPadding;
  themeOverride: WebUIContractWorkspaceThemeOverride | null;
}

function isChineseLanguage(language?: string | null): boolean {
  return (language ?? '').toLowerCase().replace('_', '-').startsWith('zh');
}

export function getLocalizedWebUIContractTitle(
  item: Pick<WebUIContractPageListItem | WebUIContractWorkspaceListItem, 'title' | 'titleEn'>,
  language?: string | null,
): string {
  return !isChineseLanguage(language) && item.titleEn?.trim() ? item.titleEn : item.title;
}

function getLocalizedSectionLabel(section: WebUIContractWorkspaceSection, language?: string | null): string {
  return !isChineseLanguage(language) && section.labelEn?.trim() ? section.labelEn : section.label;
}

function localizePage(page: WebUIContractPageListItem, language?: string | null): WebUIContractPageListItem {
  return {
    ...page,
    title: getLocalizedWebUIContractTitle(page, language),
  };
}

function sortWorkspacePages(pages: WebUIContractPageListItem[], language?: string | null): WebUIContractPageListItem[] {
  return [...pages].sort((a, b) => (
    a.order - b.order || getLocalizedWebUIContractTitle(a, language).localeCompare(getLocalizedWebUIContractTitle(b, language))
  ));
}

export function buildWebUIContractWorkspaceSections(
  workspace: WebUIContractWorkspaceListItem,
  language?: string | null,
): WebUIContractWorkspaceSectionView[] {
  const pages = sortWorkspacePages(workspace.pages, language);
  const pageById = new Map(pages.map((page) => [page.id, page]));
  const configuredSections = workspace.sections ?? [];

  if (configuredSections.length > 0) {
    return configuredSections
      .map((section) => {
        const sectionPages = section.pageIds
          .map((pageId) => pageById.get(pageId))
          .filter((page): page is WebUIContractPageListItem => Boolean(page));
        if (sectionPages.length === 0) return null;
        const defaultPageId = section.defaultPageId && sectionPages.some((page) => page.id === section.defaultPageId)
          ? section.defaultPageId
          : sectionPages[0].id;
        return {
          id: section.id,
          label: getLocalizedSectionLabel(section, language),
          pages: sectionPages.map((page) => localizePage(page, language)),
          defaultPageId,
          contentPadding: section.contentPadding ?? 'comfortable',
          themeOverride: section.themeOverride ?? null,
        };
      })
      .filter((section): section is WebUIContractWorkspaceSectionView => section !== null);
  }

  if (pages.length === 0) return [];
  const defaultPageId = workspace.defaultPageId && pages.some((page) => page.id === workspace.defaultPageId)
    ? workspace.defaultPageId
    : pages.find((page) => page.buildStatus === 'ready')?.id ?? pages[0].id;

  return [
    {
      id: 'pages',
      label: isChineseLanguage(language) ? '页面' : 'Pages',
      pages: pages.map((page) => localizePage(page, language)),
      defaultPageId,
      contentPadding: 'comfortable',
      themeOverride: null,
    },
  ];
}
