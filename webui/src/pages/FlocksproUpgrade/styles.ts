// Shared by the Flocks Pro page and its application section. The same classes as the enterprise
// edition's page of this name, so a machine looks the same before and after it moves there.

export const NOTE = 'rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800';
export const ERROR = 'rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700';
export const DONE = 'rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700';
export const CARD = 'bg-white rounded-xl border border-gray-200 shadow-sm p-6 space-y-4 max-md:p-4';
export const BOX = 'rounded-lg border border-gray-200 px-4 py-3 space-y-3';
// What moves the machine forward: an upgrade, or taking a licence.
export const GO =
  'inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-gray-300';
// Signing in, applying, sending a form.
export const PRIMARY =
  'inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-gray-300 max-md:whitespace-nowrap';
export const SECONDARY =
  'inline-flex items-center gap-2 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-400';
export const DANGER =
  'inline-flex items-center gap-2 rounded-lg border border-red-300 px-3 py-2 text-sm text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-400';
export const INPUT =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-200';

function asDate(value: string | number): Date {
  return typeof value === 'number' ? new Date(value * 1000) : new Date(value);
}

export function formatDateTime(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined || value === '') return null;
  const date = asDate(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function daysRemaining(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const date = asDate(value);
  if (Number.isNaN(date.getTime())) return null;
  return Math.max(0, Math.ceil((date.getTime() - Date.now()) / 86400000));
}

/** Long enough to be cut in a line; the whole id is one click away (and in the title). */
export function compactIdentifier(value: string | null | undefined, head = 10, tail = 8): string {
  if (!value) return '-';
  if (value.length <= head + tail + 3) return value;
  return `${value.slice(0, head)}...${value.slice(-tail)}`;
}
