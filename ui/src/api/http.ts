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

/** FastAPI 422 `detail` is a list of {loc, msg}: render it as "field: msg". */
function validationText(items: unknown[]): string | undefined {
  const parts = items.map((it) => {
    if (typeof it === "string") return it;
    if (!it || typeof it !== "object") return "";
    const { loc, msg } = it as { loc?: unknown; msg?: unknown };
    if (typeof msg !== "string") return "";
    const field = Array.isArray(loc) ? loc.filter((x) => x !== "body").join(".") : "";
    return field ? `${field}: ${msg}` : msg;
  });
  const out = parts.filter(Boolean).join("; ");
  return out || undefined;
}

/** A human reason from an error body and status. Specific server `detail` strings pass through verbatim;
 * only the generic fallbacks are replaced by an actionable sentence. */
export function describeReason(status: number, statusText: string, body: unknown): string {
  if (body && typeof body === "object") {
    const b = body as { detail?: unknown; reason?: unknown };
    const r = b.detail ?? b.reason;
    if (typeof r === "string" && r && r !== statusText) return r;
    if (Array.isArray(r)) {
      const v = validationText(r);
      if (v) return v;
    } else if (r !== undefined && r !== null && typeof r !== "string") return JSON.stringify(r);
  }
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
