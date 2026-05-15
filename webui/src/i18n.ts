import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import enCommon from './locales/en-US/common.json';
import enNav from './locales/en-US/nav.json';
import enHome from './locales/en-US/home.json';
import enSession from './locales/en-US/session.json';
import enAgent from './locales/en-US/agent.json';
import enTask from './locales/en-US/task.json';
import enWorkflow from './locales/en-US/workflow.json';
import enTool from './locales/en-US/tool.json';
import enSkill from './locales/en-US/skill.json';
import enModel from './locales/en-US/model.json';
import enMcp from './locales/en-US/mcp.json';
import enConfig from './locales/en-US/config.json';
import enChannel from './locales/en-US/channel.json';
import enPermission from './locales/en-US/permission.json';
import enMonitoring from './locales/en-US/monitoring.json';
import enUpdate from './locales/en-US/update.json';
import enWorkspace from './locales/en-US/workspace.json';
import enAuth from './locales/en-US/auth.json';
import enNotification from './locales/en-US/notification.json';

import zhCommon from './locales/zh-CN/common.json';
import zhNav from './locales/zh-CN/nav.json';
import zhHome from './locales/zh-CN/home.json';
import zhSession from './locales/zh-CN/session.json';
import zhAgent from './locales/zh-CN/agent.json';
import zhTask from './locales/zh-CN/task.json';
import zhWorkflow from './locales/zh-CN/workflow.json';
import zhTool from './locales/zh-CN/tool.json';
import zhSkill from './locales/zh-CN/skill.json';
import zhModel from './locales/zh-CN/model.json';
import zhMcp from './locales/zh-CN/mcp.json';
import zhConfig from './locales/zh-CN/config.json';
import zhChannel from './locales/zh-CN/channel.json';
import zhPermission from './locales/zh-CN/permission.json';
import zhMonitoring from './locales/zh-CN/monitoring.json';
import zhUpdate from './locales/zh-CN/update.json';
import zhWorkspace from './locales/zh-CN/workspace.json';
import zhAuth from './locales/zh-CN/auth.json';
import zhNotification from './locales/zh-CN/notification.json';

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      'en-US': {
        common: enCommon,
        nav: enNav,
        home: enHome,
        session: enSession,
        agent: enAgent,
        task: enTask,
        workflow: enWorkflow,
        tool: enTool,
        skill: enSkill,
        model: enModel,
        mcp: enMcp,
        config: enConfig,
        channel: enChannel,
        permission: enPermission,
        monitoring: enMonitoring,
        update: enUpdate,
        workspace: enWorkspace,
        auth: enAuth,
        notification: enNotification,
      },
      'zh-CN': {
        common: zhCommon,
        nav: zhNav,
        home: zhHome,
        session: zhSession,
        agent: zhAgent,
        task: zhTask,
        workflow: zhWorkflow,
        tool: zhTool,
        skill: zhSkill,
        model: zhModel,
        mcp: zhMcp,
        config: zhConfig,
        channel: zhChannel,
        permission: zhPermission,
        monitoring: zhMonitoring,
        update: zhUpdate,
        workspace: zhWorkspace,
        auth: zhAuth,
        notification: zhNotification,
      },
    },
    fallbackLng: 'en-US',
    defaultNS: 'common',
    ns: ['common', 'nav', 'home', 'session', 'agent', 'task', 'workflow', 'tool', 'skill', 'model', 'mcp', 'config', 'channel', 'permission', 'monitoring', 'update', 'workspace', 'auth', 'notification'],
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'flocks-language',
      caches: ['localStorage'],
    },
    interpolation: {
      escapeValue: false,
    },
  });

export default i18n;
