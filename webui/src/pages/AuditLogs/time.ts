/** Convert a datetime-local input to the UTC text format used by the audit sink. */
export function toAuditUtcTimestamp(value: string): string | undefined {
  if (!value) return undefined;
  const parts = value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/,
  );
  if (!parts) throw new RangeError('Invalid audit filter date and time');

  const [, year, month, day, hour, minute, second = '0', fraction = '0'] = parts;
  const parsed = new Date(value);
  // Date can normalize impossible dates (for example February 30). Do not silently
  // change the requested range or drop the filter when the input is invalid.
  if (
    Number.isNaN(parsed.getTime())
    || parsed.getFullYear() !== Number(year)
    || parsed.getMonth() + 1 !== Number(month)
    || parsed.getDate() !== Number(day)
    || parsed.getHours() !== Number(hour)
    || parsed.getMinutes() !== Number(minute)
    || parsed.getSeconds() !== Number(second)
    || parsed.getMilliseconds() !== Number(fraction.padEnd(3, '0'))
  ) {
    throw new RangeError('Invalid audit filter date and time');
  }

  // Both bounds represent exactly the selected instant; do not round an end
  // minute up to :59. Keep a supplied fraction, but omit unnecessary .000.
  return parsed.toISOString().replace('T', ' ').replace(/(?:\.000)?Z$/, '');
}
