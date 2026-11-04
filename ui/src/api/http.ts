/** Errors carry the API's own reason, so the UI can show it rather than inventing one. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly reason: string,
  ) {
    super(reason);
  }
}

export async function reasonOf(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown; reason?: unknown };
    const r = body.detail ?? body.reason;
    if (typeof r === "string") return r;
    if (r !== undefined) return JSON.stringify(r);
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}`;
}
