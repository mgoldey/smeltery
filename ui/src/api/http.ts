/** Errors carry the API's own reason, so the UI can show it rather than inventing one. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly reason: string,
  ) {
    super(reason);
  }
}

export const NETWORK_REASON = "Cannot reach the Bench server. Is `smeltery bench` still running?";

const FALLBACKS: Record<number, string> = {
  404: "Not found. It may have been moved or deleted; reload the page.",
  409: "That conflicts with the current state. Reload and try again.",
  413: "That file is too large for the server.",
  422: "The server rejected the input. Check the values and try again.",
};

/** A human reason from an error body and status. Every API error is `{reason}` (the `Problem` model), so a server
 * reason passes through verbatim; only a missing or generic one (no body, a proxy's page, a status-text echo) is
 * replaced by an actionable sentence. */
export function describeReason(status: number, statusText: string, body: unknown): string {
  const reason = (body as { reason?: unknown } | null)?.reason;
  if (typeof reason === "string" && reason && reason !== statusText) return reason;
  if (status >= 500) return `The server hit an error (${status}). Check the terminal running \`smeltery bench\` for the traceback.`;
  return FALLBACKS[status] ?? `${status} ${statusText}`.trim();
}

export async function reasonOf(res: Response): Promise<string> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  return describeReason(res.status, res.statusText, body);
}
