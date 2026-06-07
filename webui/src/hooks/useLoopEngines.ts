import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import client from '@/api/client';

export interface LoopEngine {
  id: string;
  name: string;
  description?: string;
}

/** i18n overrides for well-known engine IDs (name + description). */
const ENGINE_I18N: Record<string, { en: { name: string; desc: string }; zh: { name: string; desc: string } }> = {
  native: {
    en: { name: 'Native', desc: 'Flocks native async loop — multi-session concurrency, minimal overhead' },
    zh: { name: 'Native', desc: 'Flocks 原生异步循环，多会话并发优先，低延迟' },
  },
  raptor: {
    en: { name: 'Raptor', desc: 'High-performance loop · parallel tool calls · parallel sub-agent execution · cross-turn prompt cache' },
    zh: { name: 'Raptor', desc: '高性能循环 · 并行工具调用 · 并行子 Agent 执行 · 跨轮 Prompt Cache' },
  },
};

function localizeEngine(engine: LoopEngine, lang: string): LoopEngine {
  const override = ENGINE_I18N[engine.id];
  if (!override) return engine;
  const locale = lang.startsWith('zh') ? 'zh' : 'en';
  return {
    ...engine,
    name: override[locale].name,
    description: override[locale].desc,
  };
}

/**
 * Fetch available agent loop engines from GET /api/engine/list.
 * Names and descriptions are localized using built-in i18n overrides for
 * well-known engine IDs (native, raptor). Unknown engines fall back to the
 * server-provided strings.
 */
export function useLoopEngines() {
  const { i18n } = useTranslation();
  const [engines, setEngines] = useState<LoopEngine[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    client
      .get<LoopEngine[]>('/api/engine/list')
      .then((res) => {
        if (!cancelled) {
          const raw = Array.isArray(res.data) ? res.data : [];
          setEngines(raw.map((e) => localizeEngine(e, i18n.language)));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setEngines([localizeEngine({ id: 'native', name: 'Native', description: '' }, i18n.language)]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [i18n.language]);

  return { engines, loading };
}
