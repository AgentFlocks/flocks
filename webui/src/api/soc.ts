import client, { getApiBase } from './client';

export interface SocUserDefinedPageDataSource {
  type: 'user-defined-page-api';
  pageId: string;
  endpoint: string;
  params?: Record<string, string | number | boolean | undefined>;
}

export type SocAlertDataSource = SocUserDefinedPageDataSource;

export const defaultSocAlertDataSource: SocAlertDataSource = {
  type: 'user-defined-page-api',
  pageId: 'alert-denoise-triage-dashboard',
  endpoint: '/stats',
};

function normalizePageApiPath(endpoint: string) {
  return endpoint.replace(/^\/+/, '').replace(/\/+$/, '');
}

export const socAPI = {
  getAlertOperationsData: (source: SocAlertDataSource = defaultSocAlertDataSource) => {
    const apiPath = normalizePageApiPath(source.endpoint);
    return client.get<unknown>(
      `/api/user-defined-pages/${encodeURIComponent(source.pageId)}/api/${apiPath}`,
      { params: source.params },
    );
  },

  getUserDefinedPageAssetUrl: (pageId: string, assetPath: string) => {
    const encodedPath = assetPath
      .split('/')
      .filter(Boolean)
      .map((part) => encodeURIComponent(part))
      .join('/');
    return `${getApiBase()}/api/user-defined-pages/${encodeURIComponent(pageId)}/assets/${encodedPath}`;
  },
};
