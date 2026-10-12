import { describe, expect, it } from 'vitest';
import i18n, { preloadI18nNamespaces } from './i18n';
import enHome from './locales/en-US/home.json';
import enNav from './locales/en-US/nav.json';
import enSession from './locales/en-US/session.json';
import zhHome from './locales/zh-CN/home.json';
import zhNav from './locales/zh-CN/nav.json';
import zhSession from './locales/zh-CN/session.json';
import enVoice from './locales/en-US/voice.json';
import zhVoice from './locales/zh-CN/voice.json';

describe('i18n lazy namespaces', () => {
  it('loads shared voice translations without an enterprise registry', async () => {
    await i18n.changeLanguage('zh-CN');
    await preloadI18nNamespaces(['voice']);
    expect(i18n.t('settings.title', { ns: 'voice' })).toBe(zhVoice.settings.title);
    expect(i18n.t('errors.audio_too_quiet', { ns: 'voice', lng: 'en-US' })).toBe(enVoice.errors.audio_too_quiet);
  });

  it('loads route namespaces on demand instead of bundling them up front', async () => {
    await i18n.changeLanguage('en-US');
    i18n.removeResourceBundle('en-US', 'workflow');

    expect(i18n.hasResourceBundle('en-US', 'home')).toBe(true);
    expect(i18n.hasResourceBundle('en-US', 'workflow')).toBe(false);

    await preloadI18nNamespaces(['workflow']);

    expect(i18n.hasResourceBundle('en-US', 'workflow')).toBe(true);
    expect(Object.keys(i18n.getResourceBundle('en-US', 'workflow'))).not.toHaveLength(0);
  });

  it('uses workbench and task terminology for session management', () => {
    expect(zhNav.sessions).toBe('工作台');
    expect(zhNav.workspace).toBe('文件目录');
    expect(zhHome.quickActions.sessions.title).toBe('工作台');
    expect(zhSession.managementTitle).toBe('工作台');
    expect(zhSession.sessionCount).toBe('{{count}} 个任务');
    expect(zhSession.collapseLoaded).toBe('收起');
    // The blank chat is titled after the sidebar entry that opens it.
    expect(zhSession.newSession).toBe('Rex 新会话');
    expect(zhSession.createSession).toBe('新建任务');
    expect(zhSession.createSessionInProject).toBe('在项目 {{project}} 中新建任务');
    expect(zhSession.projectDialog.newSessionAction).toBe('新建任务');
    expect(zhSession.filterConversations).toBe('搜索任务');
    expect(zhSession.noResults).toBe('没有匹配的任务');

    expect(enNav.sessions).toBe('Workbench');
    expect(enNav.workspace).toBe('File Directory');
    expect(enHome.quickActions.sessions.title).toBe('Workbench');
    expect(enSession.managementTitle).toBe('Workbench');
    expect(enSession.sessionCount).toBe('{{count}} tasks');
    expect(enSession.collapseLoaded).toBe('Show less');
    expect(enSession.newSession).toBe('New Rex session');
    expect(enSession.createSession).toBe('New Task');
    expect(enSession.createSessionInProject).toBe('New task in project {{project}}');
    expect(enSession.projectDialog.newSessionAction).toBe('New task');
    expect(enSession.filterConversations).toBe('Search tasks');
    expect(enSession.noResults).toBe('No matching tasks');
  });
});
