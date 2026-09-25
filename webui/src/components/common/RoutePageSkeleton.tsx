import { useDelayedVisible } from '@/hooks/useDelayedVisible';

interface RoutePageSkeletonProps {
  delayMs?: number;
}

export default function RoutePageSkeleton({ delayMs = 0 }: RoutePageSkeletonProps) {
  const visible = useDelayedVisible(delayMs);
  if (!visible) return null;

  return (
    <div data-testid="route-page-skeleton" className="space-y-6 animate-pulse">
      <div className="flex items-start justify-between gap-6">
        <div className="space-y-3">
          <div className="h-8 w-56 rounded-xl bg-selected" />
          <div className="h-4 w-80 max-w-[70vw] rounded-full bg-subtle" />
        </div>
        <div className="flex gap-2">
          <div className="h-10 w-28 rounded-xl bg-selected" />
          <div className="h-10 w-24 rounded-xl bg-subtle" />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="h-36 rounded-card border border-line bg-surface shadow-sm" />
        <div className="h-36 rounded-card border border-line bg-surface shadow-sm" />
        <div className="h-36 rounded-card border border-line bg-surface shadow-sm" />
      </div>

      <div className="rounded-card border border-line bg-surface p-4 shadow-sm">
        <div className="mb-4 flex items-center justify-between">
          <div className="h-5 w-40 rounded-full bg-selected" />
          <div className="h-9 w-52 rounded-xl bg-subtle" />
        </div>
        <div className="space-y-3">
          <div className="h-14 rounded-xl bg-subtle" />
          <div className="h-14 rounded-xl bg-canvas" />
          <div className="h-14 rounded-xl bg-subtle" />
          <div className="h-14 rounded-xl bg-canvas" />
        </div>
      </div>
    </div>
  );
}
