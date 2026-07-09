import { useEffect, useRef, useCallback, useState, type MutableRefObject } from 'react';

export interface SSEEvent {
  type: string;
  properties: any;
}

export interface UseSSEOptions {
  url: string;
  onEvent: (event: SSEEvent) => void;
  onError?: (error: Event) => void;
  onReconnect?: () => void;
  enabled?: boolean;
  /** 是否携带凭据（cookie），默认 true 以支持受保护 SSE 接口 */
  withCredentials?: boolean;
  /** 重连配置 */
  reconnect?: {
    /** 是否启用自动重连，默认 true */
    enabled?: boolean;
    /** 最大重连次数，默认 10 */
    maxRetries?: number;
    /** 初始重连延迟(ms)，默认 1000 */
    initialDelay?: number;
    /** 最大重连延迟(ms)，默认 30000 */
    maxDelay?: number;
  };
}

export type SSEConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'reconnecting' | 'failed';

type SSEReconnectConfig = Required<NonNullable<UseSSEOptions['reconnect']>>;

interface SharedSSESubscriber {
  id: number;
  paused: boolean;
  onEventRef: MutableRefObject<UseSSEOptions['onEvent']>;
  onErrorRef: MutableRefObject<UseSSEOptions['onError']>;
  onReconnectRef: MutableRefObject<UseSSEOptions['onReconnect']>;
  reconnect: SSEReconnectConfig;
  setStatus: (status: SSEConnectionStatus) => void;
  setRetryCount: (count: number) => void;
}

interface SharedSSEConnection {
  key: string;
  url: string;
  withCredentials: boolean;
  eventSource: EventSource | null;
  reconnectTimeout: number | null;
  retryCount: number;
  status: SSEConnectionStatus;
  subscribers: Map<number, SharedSSESubscriber>;
}

const sharedConnections = new Map<string, SharedSSEConnection>();
let nextSubscriberId = 1;

function connectionKey(url: string, withCredentials: boolean): string {
  return `${withCredentials ? 'auth' : 'anon'}:${url}`;
}

function hasActiveSubscribers(connection: SharedSSEConnection): boolean {
  for (const subscriber of connection.subscribers.values()) {
    if (!subscriber.paused) return true;
  }
  return false;
}

function forEachActiveSubscriber(
  connection: SharedSSEConnection,
  callback: (subscriber: SharedSSESubscriber) => void,
) {
  for (const subscriber of connection.subscribers.values()) {
    if (!subscriber.paused) callback(subscriber);
  }
}

function notifyStatus(connection: SharedSSEConnection, status: SSEConnectionStatus) {
  connection.status = status;
  forEachActiveSubscriber(connection, (subscriber) => subscriber.setStatus(status));
}

function notifyRetryCount(connection: SharedSSEConnection) {
  forEachActiveSubscriber(connection, (subscriber) => subscriber.setRetryCount(connection.retryCount));
}

function mergedReconnectConfig(connection: SharedSSEConnection): SSEReconnectConfig {
  let reconnecting = 0;
  let maxRetries = 0;
  let initialDelay = Number.POSITIVE_INFINITY;
  let maxDelay = Number.POSITIVE_INFINITY;

  forEachActiveSubscriber(connection, (subscriber) => {
    if (!subscriber.reconnect.enabled) return;
    reconnecting += 1;
    maxRetries = Math.max(maxRetries, subscriber.reconnect.maxRetries);
    initialDelay = Math.min(initialDelay, subscriber.reconnect.initialDelay);
    maxDelay = Math.min(maxDelay, subscriber.reconnect.maxDelay);
  });

  if (reconnecting === 0) {
    return { enabled: false, maxRetries: 0, initialDelay: 1000, maxDelay: 30000 };
  }

  return {
    enabled: true,
    maxRetries,
    initialDelay,
    maxDelay,
  };
}

function clearReconnectTimeout(connection: SharedSSEConnection) {
  if (connection.reconnectTimeout !== null) {
    window.clearTimeout(connection.reconnectTimeout);
    connection.reconnectTimeout = null;
  }
}

function getReconnectDelay(config: SSEReconnectConfig, retryCount: number) {
  const delay = Math.min(config.initialDelay * Math.pow(2, retryCount), config.maxDelay);
  return delay + Math.random() * 1000;
}

function closeEventSource(connection: SharedSSEConnection) {
  if (connection.eventSource) {
    connection.eventSource.close();
    connection.eventSource = null;
  }
}

function cleanupInactiveConnection(connection: SharedSSEConnection) {
  if (hasActiveSubscribers(connection)) return;
  clearReconnectTimeout(connection);
  closeEventSource(connection);
  connection.status = 'disconnected';
  connection.retryCount = 0;
  if (connection.subscribers.size === 0) {
    sharedConnections.delete(connection.key);
  }
}

function connectShared(connection: SharedSSEConnection) {
  if (!hasActiveSubscribers(connection)) {
    cleanupInactiveConnection(connection);
    return;
  }

  clearReconnectTimeout(connection);
  closeEventSource(connection);

  if (import.meta.env.DEV) {
    console.log('[SSE] Creating shared EventSource connection to:', connection.url);
  }
  notifyStatus(connection, 'connecting');

  const eventSource = new EventSource(connection.url, { withCredentials: connection.withCredentials });
  connection.eventSource = eventSource;

  eventSource.onopen = () => {
    if (connection.eventSource !== eventSource) return;
    if (import.meta.env.DEV) {
      console.log('[SSE] Shared connection opened successfully');
    }
    connection.retryCount = 0;
    notifyRetryCount(connection);
    notifyStatus(connection, 'connected');
  };

  eventSource.onmessage = (event) => {
    if (connection.eventSource !== eventSource) return;
    try {
      const data = JSON.parse(event.data);
      forEachActiveSubscriber(connection, (subscriber) => {
        subscriber.onEventRef.current(data);
      });
    } catch (err) {
      console.error('Failed to parse SSE event:', err);
    }
  };

  eventSource.onerror = (error) => {
    if (connection.eventSource !== eventSource) return;

    if (import.meta.env.DEV) {
      console.warn('[SSE] Shared connection error, will attempt to reconnect');
    }
    forEachActiveSubscriber(connection, (subscriber) => {
      subscriber.onErrorRef.current?.(error);
    });

    eventSource.close();
    connection.eventSource = null;

    const reconnectConfig = mergedReconnectConfig(connection);
    if (!reconnectConfig.enabled) {
      notifyStatus(connection, 'failed');
      return;
    }

    if (connection.retryCount < reconnectConfig.maxRetries) {
      const delay = getReconnectDelay(reconnectConfig, connection.retryCount);
      if (import.meta.env.DEV) {
        console.log(`[SSE] Reconnecting shared connection in ${Math.round(delay)}ms (attempt ${connection.retryCount + 1}/${reconnectConfig.maxRetries})`);
      }
      notifyStatus(connection, 'reconnecting');

      clearReconnectTimeout(connection);
      connection.reconnectTimeout = window.setTimeout(() => {
        if (!hasActiveSubscribers(connection)) {
          cleanupInactiveConnection(connection);
          return;
        }
        connection.retryCount++;
        notifyRetryCount(connection);
        forEachActiveSubscriber(connection, (subscriber) => {
          subscriber.onReconnectRef.current?.();
        });
        connectShared(connection);
      }, delay);
      return;
    }

    if (import.meta.env.DEV) {
      console.log('[SSE] Max fast retries reached, switching shared connection to slow retry mode');
    }
    notifyStatus(connection, 'reconnecting');

    clearReconnectTimeout(connection);
    connection.reconnectTimeout = window.setTimeout(() => {
      if (!hasActiveSubscribers(connection)) {
        cleanupInactiveConnection(connection);
        return;
      }
      connection.retryCount = 0;
      notifyRetryCount(connection);
      connectShared(connection);
    }, 30000);
  };
}

function getOrCreateSharedConnection(url: string, withCredentials: boolean): SharedSSEConnection {
  const key = connectionKey(url, withCredentials);
  const existing = sharedConnections.get(key);
  if (existing) return existing;

  const connection: SharedSSEConnection = {
    key,
    url,
    withCredentials,
    eventSource: null,
    reconnectTimeout: null,
    retryCount: 0,
    status: 'disconnected',
    subscribers: new Map(),
  };
  sharedConnections.set(key, connection);
  return connection;
}

export function useSSE({ 
  url, 
  onEvent, 
  onError, 
  onReconnect,
  enabled = true,
  withCredentials = true,
  reconnect = {},
}: UseSSEOptions) {
  const subscriberIdRef = useRef<number | null>(null);
  const subscriberRef = useRef<SharedSSESubscriber | null>(null);
  const connectionRef = useRef<SharedSSEConnection | null>(null);
  const onEventRef = useRef(onEvent);
  const onErrorRef = useRef(onError);
  const onReconnectRef = useRef(onReconnect);
  
  const [status, setStatus] = useState<SSEConnectionStatus>('disconnected');
  const [retryCount, setRetryCount] = useState(0);

  const {
    enabled: reconnectEnabled = true,
    maxRetries = 10,
    initialDelay = 1000,
    maxDelay = 30000,
  } = reconnect;

  // Update refs
  useEffect(() => {
    onEventRef.current = onEvent;
    onErrorRef.current = onError;
    onReconnectRef.current = onReconnect;
  }, [onEvent, onError, onReconnect]);

  useEffect(() => {
    if (subscriberIdRef.current === null) {
      subscriberIdRef.current = nextSubscriberId++;
    }

    if (!enabled) {
      if (import.meta.env.DEV) {
        console.log('[SSE] Not enabled, skipping connection');
      }
      setStatus('disconnected');
      setRetryCount(0);
      return;
    }

    const connection = getOrCreateSharedConnection(url, withCredentials);
    const subscriber: SharedSSESubscriber = {
      id: subscriberIdRef.current,
      paused: false,
      onEventRef,
      onErrorRef,
      onReconnectRef,
      reconnect: {
        enabled: reconnectEnabled,
        maxRetries,
        initialDelay,
        maxDelay,
      },
      setStatus,
      setRetryCount,
    };

    subscriberRef.current = subscriber;
    connectionRef.current = connection;
    connection.subscribers.set(subscriber.id, subscriber);
    setStatus(connection.status);
    setRetryCount(connection.retryCount);

    if (!connection.eventSource && connection.reconnectTimeout === null) {
      connectShared(connection);
    }

    return () => {
      connection.subscribers.delete(subscriber.id);
      if (subscriberRef.current === subscriber) {
        subscriberRef.current = null;
      }
      if (connectionRef.current === connection) {
        connectionRef.current = null;
      }
      cleanupInactiveConnection(connection);
    };
  }, [
    enabled,
    initialDelay,
    maxDelay,
    maxRetries,
    reconnectEnabled,
    url,
    withCredentials,
  ]);

  const reconnectManually = useCallback(() => {
    const connection = connectionRef.current;
    const subscriber = subscriberRef.current;
    if (!connection || !subscriber || !enabled) return;
    subscriber.paused = false;
    connection.retryCount = 0;
    notifyRetryCount(connection);
    connectShared(connection);
  }, [enabled]);

  const close = useCallback(() => {
    const connection = connectionRef.current;
    const subscriber = subscriberRef.current;
    if (!connection || !subscriber) return;
    subscriber.paused = true;
    setStatus('disconnected');
    setRetryCount(0);
    cleanupInactiveConnection(connection);
  }, []);

  return {
    /** 当前连接状态 */
    status,
    /** 手动关闭连接 */
    close,
    /** 手动重连 */
    reconnect: reconnectManually,
    /** 当前重试次数 */
    retryCount,
  };
}
