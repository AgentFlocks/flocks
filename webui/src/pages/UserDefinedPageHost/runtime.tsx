import React, { type ComponentType, type ReactNode } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';
import type { AxiosRequestConfig, AxiosResponse } from 'axios';
import apiClient from '@/api/client';
import { useAuth } from '@/contexts/AuthContext';

interface UserDefinedPageScopedApi {
  get<T = unknown>(path: string, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>;
  post<T = unknown>(path: string, data?: unknown, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>;
  put<T = unknown>(path: string, data?: unknown, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>;
  patch<T = unknown>(path: string, data?: unknown, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>;
  delete<T = unknown>(path: string, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>;
}

type UserDefinedPageApiClient = typeof apiClient & {
  page: UserDefinedPageScopedApi;
};

export interface UserDefinedPageSdk {
  React: typeof React;
  jsx: typeof jsx;
  jsxs: typeof jsxs;
  api: UserDefinedPageApiClient;
  Card: typeof Card;
  useCurrentUser: typeof useCurrentUser;
}

declare global {
  interface Window {
    __FLOCKS_USER_DEFINED_PAGE_SDK__?: UserDefinedPageSdk;
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

function createScopedApi(pageId: string): UserDefinedPageScopedApi {
  const base = `/api/user-defined-pages/${encodeURIComponent(pageId)}/api`;
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

export function installUserDefinedPageRuntime(pageId: string): void {
  if (typeof window === 'undefined') return;
  const api = apiClient as UserDefinedPageApiClient;
  api.page = createScopedApi(pageId);
  window.__FLOCKS_USER_DEFINED_PAGE_SDK__ = {
    React,
    jsx,
    jsxs,
    api,
    Card,
    useCurrentUser,
  };
}

export async function loadUserDefinedPageBundle(
  url: string,
  missingExportMessage = 'Page bundle does not export a default component',
): Promise<ComponentType> {
  const response = await apiClient.get<string>(url, { responseType: 'text' });
  const source = typeof response.data === 'string' ? response.data : String(response.data ?? '');
  const moduleUrl = URL.createObjectURL(new Blob([source], { type: 'application/javascript' }));

  try {
    const mod = await import(/* @vite-ignore */ moduleUrl);
    const component = mod.default as ComponentType | undefined;
    if (!component) {
      throw new Error(missingExportMessage);
    }
    return component;
  } finally {
    URL.revokeObjectURL(moduleUrl);
  }
}
