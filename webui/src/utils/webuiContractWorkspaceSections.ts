import type { WebUIContractPageListItem, WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';

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

function sortWorkspacePages(pages: WebUIContractPageListItem[]): WebUIContractPageListItem[] {
  return [...pages].sort((a, b) => a.order - b.order || a.title.localeCompare(b.title));
}

export function buildWebUIContractWorkspaceSections(
  workspace: WebUIContractWorkspaceListItem,
): WebUIContractWorkspaceSectionView[] {
  const pages = sortWorkspacePages(workspace.pages);
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
          label: section.label,
          pages: sectionPages,
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
      label: '页面',
      pages,
      defaultPageId,
      contentPadding: 'comfortable',
      themeOverride: null,
    },
  ];
}
