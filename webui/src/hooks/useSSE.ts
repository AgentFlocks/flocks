import { useEffect, useRef, useCallback, useState } from 'react';

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

export function useSSE({ 
  url, 
  onEvent, 
  onError, 
  onReconnect,
  enabled = true,
  reconnect = {},
}: UseSSEOptions) {
  const eventSourceRef = useRef<EventSource | null>(null);
  const onEventRef = useRef(onEvent);
  const onErrorRef = useRef(onError);
  const onReconnectRef = useRef(onReconnect);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const retryCountRef = useRef(0);
  const mountedRef = useRef(true);
  
  const [status, setStatus] = useState<SSEConnectionStatus>('disconnected');

  // 重连配置
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

  // 计算指数退避延迟
  const getReconnectDelay = useCallback((retryCount: number) => {
    const delay = Math.min(initialDelay * Math.pow(2, retryCount), maxDelay);
    // 添加一些随机抖动以避免雷暴效应
    return delay + Math.random() * 1000;
  }, [initialDelay, maxDelay]);

  // 清理重连定时器
  const clearReconnectTimeout = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  // 创建连接
  const connect = useCallback(() => {
    if (!mountedRef.current || !enabled) return;

    // 清理现有连接
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    if (import.meta.env.DEV) {
      console.log('[SSE] Creating EventSource connection to:', url);
    }
    setStatus('connecting');
    const eventSource = new EventSource(url);
    eventSourceRef.current = eventSource;
    
    eventSource.onopen = () => {
      if (!mountedRef.current) return;
      if (import.meta.env.DEV) {
        console.log('[SSE] Connection opened successfully');
      }
      setStatus('connected');
      retryCountRef.current = 0; // 重置重试计数
    };

    // Handle messages
    eventSource.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(event.data);
        // Debug: 打印收到的 SSE 事件
        if (import.meta.env.DEV && data.type === 'message.part.updated') {
          console.log('[SSE] message.part.updated:', {
            partId: data.properties?.part?.id,
            textLength: data.properties?.part?.text?.length,
            deltaLength: data.properties?.delta?.length,
            delta: data.properties?.delta?.substring(0, 50),
          });
        }
        onEventRef.current(data);
      } catch (err) {
        console.error('Failed to parse SSE event:', err);
      }
    };

    // Handle errors with auto-reconnect
    eventSource.onerror = (error) => {
      if (!mountedRef.current) return;
      
      if (import.meta.env.DEV) {
        console.warn('[SSE] Connection error, will attempt to reconnect');
      }
      onErrorRef.current?.(error);
      
      // 关闭当前连接
      eventSource.close();
      eventSourceRef.current = null;
      
      // 尝试重连（使用更宽松的策略）
      if (reconnectEnabled && retryCountRef.current < maxRetries) {
        const delay = getReconnectDelay(retryCountRef.current);
        if (import.meta.env.DEV) {
          console.log(`[SSE] Reconnecting in ${Math.round(delay)}ms (attempt ${retryCountRef.current + 1}/${maxRetries})`);
        }
        setStatus('reconnecting');
        
        clearReconnectTimeout();
        reconnectTimeoutRef.current = window.setTimeout(() => {
          if (!mountedRef.current) return;
          retryCountRef.current++;
          onReconnectRef.current?.();
          connect();
        }, delay);
      } else {
        // 即使达到最大重试次数，也不要完全放弃，而是使用较长的间隔继续尝试
        if (import.meta.env.DEV) {
          console.log('[SSE] Max fast retries reached, switching to slow retry mode');
        }
        setStatus('reconnecting');
        
        clearReconnectTimeout();
        reconnectTimeoutRef.current = window.setTimeout(() => {
          if (!mountedRef.current) return;
          retryCountRef.current = 0; // 重置计数器，重新开始
          connect();
        }, 30000); // 30秒后重试
      }
    };
  }, [url, enabled, reconnectEnabled, maxRetries, getReconnectDelay, clearReconnectTimeout]);

  // 主 effect
  useEffect(() => {
    mountedRef.current = true;

    if (!enabled) {
      if (import.meta.env.DEV) {
        console.log('[SSE] Not enabled, skipping connection');
      }
      setStatus('disconnected');
      return;
    }

    connect();

    // Cleanup
    return () => {
      mountedRef.current = false;
      clearReconnectTimeout();
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [url, enabled, connect, clearReconnectTimeout]);

  // 手动重连
  const reconnectManually = useCallback(() => {
    retryCountRef.current = 0;
    connect();
  }, [connect]);

  return {
    /** 当前连接状态 */
    status,
    /** 手动关闭连接 */
    close: () => {
      clearReconnectTimeout();
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      setStatus('disconnected');
    },
    /** 手动重连 */
    reconnect: reconnectManually,
    /** 当前重试次数 */
    retryCount: retryCountRef.current,
  };
}
