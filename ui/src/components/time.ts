/**
 * Timestamps as every page shows them: read in UTC from the ISO string the API sends, never converted to the
 * viewer's local time, so a record reads the same for everyone looking at it. A string without an offset is taken
 * as UTC (the API writes UTC throughout). Anything unreadable is shown as given.
 */

function utc(iso: string): Date | null {
  const t = iso.trim();
  if (!t) return null;
  // A date-time with no Z or ±hh:mm is UTC by the API's convention; Date would otherwise read it as local time.
  const zoned = /T.*(Z|[+-]\d\d:?\d\d)$/i.test(t) || !t.includes("T") ? t : `${t}Z`;
  const d = new Date(zoned);
  return Number.isNaN(d.getTime()) ? null : d;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** `YYYY-MM-DD`, the UTC calendar day. */
export function day(iso: string): string {
  const d = utc(iso);
  return d ? `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` : iso;
}

/** `YYYY-MM-DD HH:MM UTC`. */
export function stamp(iso: string): string {
  const d = utc(iso);
  return d ? `${day(iso)} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC` : iso;
}
