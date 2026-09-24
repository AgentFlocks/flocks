import type { ReactNode } from 'react';
import registry, { type ExtensionSlotComponent, type SlotName } from '@ext/registry';

const slotCache = new Map<SlotName, ExtensionSlotComponent[]>();

/** Registered components for a slot, sorted once by `order` and cached per slot name. */
export function getSlotComponents(name: SlotName): ExtensionSlotComponent[] {
  const cached = slotCache.get(name);
  if (cached) return cached;
  const components = registry.slots
    .filter((entry) => entry.slot === name)
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  slotCache.set(name, components);
  return components;
}

/** Test hook: forget cached slot lookups (registry mocks change between tests). */
export function __resetExtensionSlotCacheForTesting() {
  slotCache.clear();
}

interface ExtensionSlotProps {
  name: SlotName;
  /** Values handed to every registered component for this slot. */
  context?: Record<string, unknown>;
  /** Default content rendered when no extension registered for the slot. */
  children?: ReactNode;
}

/**
 * Renders the components an extension registered for `name`. Without a
 * registration it renders `children` (the default content), or nothing.
 */
export default function ExtensionSlot({ name, context, children }: ExtensionSlotProps) {
  const components = getSlotComponents(name);
  if (components.length === 0) {
    return <>{children ?? null}</>;
  }
  const slotContext = context ?? {};
  return (
    <>
      {components.map(({ id, component: Component }) => (
        <Component key={id} context={slotContext} />
      ))}
    </>
  );
}
