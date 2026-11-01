// Shared by the Flocks Pro page and its application section.

export const NOTE = 'rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800';
export const ERROR = 'rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700';
// The product's own call-to-action red, which keeps its fill in dark mode (a slate fill turns into the card's).
export const PRIMARY =
  'inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40';
export const SECONDARY =
  'inline-flex items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60';
export const CARD = 'rounded-xl border border-gray-200 bg-white p-5 space-y-4';
export const INPUT =
  'w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm';
export const LABEL = 'block text-sm font-medium text-gray-700 mb-1';

export function formatDate(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined || value === '') return null;
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString();
}
