import axios from 'axios';

export const DEFAULT_API_TIMEOUT_MS = 30000;

function isLoopbackHostname(hostname: string): boolean {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1';
}

export function resolveApiBaseURL(configuredBaseURL: string, currentOrigin?: string): string {
  if (!configuredBaseURL || !currentOrigin) {
    return configuredBaseURL;
  }

  try {
    const configuredUrl = new URL(configuredBaseURL);
    const currentUrl = new URL(currentOrigin);

    if (
      configuredUrl.origin !== currentUrl.origin &&
      isLoopbackHostname(configuredUrl.hostname) &&
      isLoopbackHostname(currentUrl.hostname)
    ) {
      configuredUrl.hostname = currentUrl.hostname;
      return configuredUrl.toString().replace(/\/$/, '');
    }

    return configuredBaseURL;
  } catch {
    return configuredBaseURL;
  }
}

// 部署时前后端同域，使用相对路径即可；本地开发若混用 localhost/127.0.0.1，
// 这里会自动对齐到当前页面主机名，避免浏览器把登录 cookie 当成跨站请求。
const baseURL = resolveApiBaseURL(
  import.meta.env.VITE_API_BASE_URL || '',
  typeof window !== 'undefined' ? window.location.origin : undefined,
);

export const apiClient = axios.create({
  baseURL,
  timeout: DEFAULT_API_TIMEOUT_MS, // 30 seconds - 缩短超时时间以更快发现连接问题
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

export function shouldDisableApiTimeout(url?: string, method?: string): boolean {
  if (!url) return false;

  const normalizedMethod = (method || 'get').toLowerCase();
  const path = (() => {
    try {
      return new URL(url, 'http://flocks.local').pathname;
    } catch {
      return url.split('?')[0] || url;
    }
  })();

  if (normalizedMethod === 'post' && path === '/api/session') {
    return true;
  }

  if (path.startsWith('/api/session/')) {
    return (
      ['post', 'patch', 'delete'].includes(normalizedMethod) &&
      (
        path.endsWith('/message') ||
        path.endsWith('/prompt_async') ||
        path.includes('/prompt_queue') ||
        path.endsWith('/command') ||
        path.endsWith('/abort')
      )
    );
  }

  if (normalizedMethod === 'post' && path.startsWith('/api/question/')) {
    return path.endsWith('/reply') || path.endsWith('/reject');
  }

  return false;
}

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    if (shouldDisableApiTimeout(config.url, config.method)) {
      config.timeout = 0;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    return response;
  },
  (error) => {
    const status = error.response?.status;
    const url = error.config?.url || '';
    const isAuthEndpoint =
      typeof url === 'string' &&
      (
        url.includes('/api/auth/login') ||
        url.includes('/api/auth/bootstrap-status') ||
        url.includes('/api/auth/bootstrap-admin')
      );
    const isExpectedMissingDefaultModel =
      status === 404 && typeof url === 'string' && url.includes('/api/default-model/resolved');

    if (isExpectedMissingDefaultModel) {
      return Promise.reject(error);
    }

    if (status === 401 && !isAuthEndpoint) {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new Event('flocks:auth-expired'));
      }
      return Promise.reject(error);
    }

    // 统一错误处理
    if (error.code === 'ECONNABORTED') {
      console.error('API Timeout:', error.config?.url);
    } else if (error.code === 'ERR_NETWORK') {
      console.error('Network Error - Backend may be restarting:', error.config?.url);
    } else {
      console.error('API Error:', error.response?.data || error.message);
    }
    return Promise.reject(error);
  }
);

/** Returns the configured API base URL (empty string means same origin). */
export function getApiBase(): string {
  return baseURL;
}

// 默认导出
export default apiClient;
