import i18n from 'i18next';

/** Resolve shared labels at render time so page-local icon buttons follow the current language. */
const additionalLabels = {
  close: ['关闭', 'Close'], delete: ['删除', 'Delete'], edit: ['编辑', 'Edit'],
  refresh: ['刷新', 'Refresh'], copy: ['复制', 'Copy'], save: ['保存', 'Save'], create: ['创建', 'Create'],
  previous: ['上一页', 'Previous page'], next: ['下一页', 'Next page'],
  back: ['返回', 'Back'], filter: ['筛选', 'Filter'],
  expand: ['展开', 'Expand'], collapse: ['收起', 'Collapse'],
  showPassword: ['显示密码', 'Show password'], hidePassword: ['隐藏密码', 'Hide password'],
} as const;

export function pageActionLabel(action: keyof typeof additionalLabels): string {
  const fallback = additionalLabels[action][i18n.language?.toLowerCase().startsWith('zh') ? 0 : 1];
  return i18n.t(`common:button.${action}`, { defaultValue: fallback }) || fallback;
}
