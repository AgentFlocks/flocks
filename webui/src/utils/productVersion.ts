/**
 * A product version as menus and pages show it: v<year>.<month>.<day>.<build> (v2026.11.09.1), month and
 * day in two digits, the build number last and .0 for a version without one (a source install's
 * 2026.11.09). It reads what installs, Console and the packages record -- 2026.11.9, v2026.11.09,
 * pro-v2026.11.09.1, 2026.11.09-2 -- and shows anything that is not such a version as it came, with a v
 * before a leading digit. Null for nothing at all.
 */
export function formatProductVersion(raw?: string | null): string | null {
  const text = String(raw ?? '').trim();
  if (!text) return null;
  const match = /^(?:[a-z]+-)?v?(\d{4})[.\-_](\d{1,2})[.\-_](\d{1,2})(?:[.\-_](\d+))?(?![\d])/i.exec(text);
  if (!match) return /^\d/.test(text) ? `v${text}` : text;
  const [, year, month, day, build] = match;
  return `v${year}.${month.padStart(2, '0')}.${day.padStart(2, '0')}.${build ?? '0'}`;
}
