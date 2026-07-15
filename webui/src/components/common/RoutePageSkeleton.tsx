export default function RoutePageSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="flex items-start justify-between gap-6">
        <div className="space-y-3">
          <div className="h-8 w-56 rounded-xl bg-gray-200" />
          <div className="h-4 w-80 max-w-[70vw] rounded-full bg-gray-100" />
        </div>
        <div className="flex gap-2">
          <div className="h-10 w-28 rounded-xl bg-gray-200" />
          <div className="h-10 w-24 rounded-xl bg-gray-100" />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="h-36 rounded-2xl border border-gray-200 bg-white shadow-sm" />
        <div className="h-36 rounded-2xl border border-gray-200 bg-white shadow-sm" />
        <div className="h-36 rounded-2xl border border-gray-200 bg-white shadow-sm" />
      </div>

      <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="mb-4 flex items-center justify-between">
          <div className="h-5 w-40 rounded-full bg-gray-200" />
          <div className="h-9 w-52 rounded-xl bg-gray-100" />
        </div>
        <div className="space-y-3">
          <div className="h-14 rounded-xl bg-gray-100" />
          <div className="h-14 rounded-xl bg-gray-50" />
          <div className="h-14 rounded-xl bg-gray-100" />
          <div className="h-14 rounded-xl bg-gray-50" />
        </div>
      </div>
    </div>
  );
}
