import { useEffect, useState } from 'react';
import { defaultModelAPI, modelV2API } from '@/api/provider';

/**
 * Detect whether the resolved default LLM model supports image (vision) input.
 *
 * Returns:
 *   - `true`  — model is multimodal / supports images
 *   - `false` — model explicitly does not support images (UI should block image
 *               uploads with a warning)
 *   - `null`  — unknown / unable to determine (UI should allow uploads as a
 *               best-effort fallback)
 *
 * Centralised so every place that hosts a chat composer (Session, Agent /
 * Workflow / Skill / Tool creation drawers, generic ChatDialog, etc.) gets
 * the same logic and the same UX. Without this, only the Session page
 * showed the "current model does not support images" hint, while uploading
 * an image in the other composers would silently fail (or send through to
 * a non-vision model).
 *
 * Caching:
 *   The resolved capability is cached at module scope so each newly mounted
 *   composer (sidebar drawer, dialog, etc.) reuses the in-flight or
 *   completed lookup instead of firing a fresh `getResolved + getDefinition`
 *   pair. The cache is invalidated when ``MODEL_CHANGED_EVENT`` fires —
 *   pages that change the default model (see ``Model/index.tsx``) dispatch
 *   that event after a successful update so this hook re-resolves.
 */

/** Window event other code can dispatch to invalidate the cached vision capability. */
export const MODEL_CHANGED_EVENT = 'flocks:default-model-changed';

type VisionState = boolean | null;

let cachedPromise: Promise<VisionState> | null = null;
const subscribers = new Set<(state: VisionState) => void>();

async function resolveVisionSupport(): Promise<VisionState> {
  try {
    const resolvedResp = await defaultModelAPI.getResolved();
    const { provider_id, model_id } = resolvedResp.data;
    if (!provider_id || !model_id) return null;
    const defResp = await modelV2API.getDefinition(provider_id, model_id);
    const def: any = defResp.data;
    // Predefined (catalog/SDK) models have vision handled at the provider
    // level.  We intentionally treat them as non-vision here so that only
    // user-added (fetch_from === 'customizable') models that have explicitly
    // enabled vision trigger the multimodal upload flow.
    if (!def || def.fetch_from !== 'customizable') return null;
    const caps = def.capabilities;
    if (!caps) return null;
    if (
      caps.supports_vision === true ||
      caps.modalities?.input?.includes('image') ||
      (caps.features ?? []).includes('vision')
    ) {
      return true;
    }
    if (caps.supports_vision === false) {
      return false;
    }
    return null;
  } catch {
    return null;
  }
}

function getVisionPromise(): Promise<VisionState> {
  if (cachedPromise === null) {
    cachedPromise = resolveVisionSupport();
  }
  return cachedPromise;
}

function invalidateAndRefetch(): void {
  // Capture the new promise locally so a *second* rapid invalidation that
  // races ahead of this one cannot deliver a stale value to subscribers.
  // We only notify if our promise is still the current cached one by the
  // time it resolves.
  const next = resolveVisionSupport();
  cachedPromise = next;
  next.then((value) => {
    if (cachedPromise === next) {
      subscribers.forEach((cb) => cb(value));
    }
  });
}

if (typeof window !== 'undefined') {
  window.addEventListener(MODEL_CHANGED_EVENT, invalidateAndRefetch);
}

/**
 * Test-only escape hatch: clear the module-level cache and subscriber set.
 * Vitest runs all specs in the same module instance, so without this a
 * stubbed API response from one test could leak into the next. Production
 * code should never call this.
 */
export function __resetVisionCacheForTesting(): void {
  cachedPromise = null;
  subscribers.clear();
}

export function useDefaultModelVision(): VisionState {
  const [supportsVision, setSupportsVision] = useState<VisionState>(null);

  useEffect(() => {
    let cancelled = false;
    getVisionPromise().then((next) => {
      if (!cancelled) setSupportsVision(next);
    });
    subscribers.add(setSupportsVision);
    return () => {
      cancelled = true;
      subscribers.delete(setSupportsVision);
    };
  }, []);

  return supportsVision;
}
