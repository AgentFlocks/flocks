import React, { type ComponentType, type ReactNode } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';
import type { AxiosRequestConfig, AxiosResponse } from 'axios';
import apiClient from '@/api/client';
import { useAuth } from '@/contexts/AuthContext';

interface WebUIContractPageScopedApi {
  get<T = unknown>(path: string, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>;
  post<T = unknown>(path: string, data?: unknown, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>;
  put<T = unknown>(path: string, data?: unknown, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>;
  patch<T = unknown>(path: string, data?: unknown, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>;
  delete<T = unknown>(path: string, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>;
}

interface WebUIContractOperationApi {
  operation<T = unknown>(
    operationName: string,
    data?: unknown,
    config?: AxiosRequestConfig,
  ): Promise<AxiosResponse<T>>;
}

type WebUIContractPageApiClient = typeof apiClient & {
  page: WebUIContractPageScopedApi;
  contract(pagePath: string, contractId: string): WebUIContractOperationApi;
};

export interface WebUIContractPageSdk {
  React: typeof React;
  jsx: typeof jsx;
  jsxs: typeof jsxs;
  api: WebUIContractPageApiClient;
  Card: typeof Card;
  useCurrentUser: typeof useCurrentUser;
}

declare global {
  interface Window {
    __FLOCKS_WEBUI_CONTRACT_SDK__?: WebUIContractPageSdk;
  }
}

export function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      <h2 className="mb-2 text-lg font-semibold text-zinc-900">{title}</h2>
      <div className="text-sm text-zinc-700">{children}</div>
    </div>
  );
}

export function useCurrentUser() {
  const { user } = useAuth();
  return user;
}

function normalizePageApiPath(path: string): string {
  if (!path) return '/';
  return path.startsWith('/') ? path : `/${path}`;
}

function encodePagePath(pagePath: string): string {
  return pagePath
    .split('/')
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join('/');
}

function createScopedApi(pageId: string): WebUIContractPageScopedApi {
  const base = `/api/contracts/webui/pages/${encodeURIComponent(pageId)}/api`;
  return {
    get(path, config) {
      return apiClient.get(`${base}${normalizePageApiPath(path)}`, config);
    },
    post(path, data, config) {
      return apiClient.post(`${base}${normalizePageApiPath(path)}`, data, config);
    },
    put(path, data, config) {
      return apiClient.put(`${base}${normalizePageApiPath(path)}`, data, config);
    },
    patch(path, data, config) {
      return apiClient.patch(`${base}${normalizePageApiPath(path)}`, data, config);
    },
    delete(path, config) {
      return apiClient.delete(`${base}${normalizePageApiPath(path)}`, config);
    },
  };
}

function createContractApi(pagePath: string, contractId: string): WebUIContractOperationApi {
  const base = `/api/contracts/webui/pages/${encodePagePath(pagePath)}/access/${encodeURIComponent(contractId)}/operations`;
  return {
    operation(operationName, data, config) {
      return apiClient.post(`${base}/${encodeURIComponent(operationName)}`, data, config);
    },
  };
}

function createPageApiClient(pageId: string): WebUIContractPageApiClient {
  // One client object per page. The bundle's SDK shim captures `api` when its
  // module evaluates, so pages kept alive side by side (SOC 告警态势 while
  // 告警调查 is open) each keep their own `api.page` scope instead of the
  // last installed page hijacking everyone's page API calls.
  const api = Object.create(apiClient) as WebUIContractPageApiClient;
  api.page = createScopedApi(pageId);
  api.contract = createContractApi;
  return api;
}

export function installWebUIContractPageRuntime(pageId: string): void {
  if (typeof window === 'undefined') return;
  window.__FLOCKS_WEBUI_CONTRACT_SDK__ = {
    React,
    jsx,
    jsxs,
    api: createPageApiClient(pageId),
    Card,
    useCurrentUser,
  };
}

// Bundles evaluate one at a time: the SDK global must still name the page a
// bundle belongs to when that bundle reads it, even while several kept-alive
// panes load at once.
let bundleImportQueue: Promise<unknown> = Promise.resolve();

export async function loadWebUIContractPageBundle(
  url: string,
  missingExportMessage = 'Page bundle does not export a default component',
  pageId?: string,
): Promise<ComponentType> {
  const response = await apiClient.get<string>(url, { responseType: 'text' });
  const source = typeof response.data === 'string' ? response.data : String(response.data ?? '');
  const moduleUrl = URL.createObjectURL(new Blob([source], { type: 'application/javascript' }));

  const load = bundleImportQueue.then(async () => {
    try {
      if (pageId) installWebUIContractPageRuntime(pageId);
      const mod = await import(/* @vite-ignore */ moduleUrl);
      const component = mod.default as ComponentType | undefined;
      if (!component) {
        throw new Error(missingExportMessage);
      }
      return component;
    } finally {
      URL.revokeObjectURL(moduleUrl);
    }
  });
  bundleImportQueue = load.catch(() => undefined);
  return load;
}
