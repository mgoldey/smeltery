/**
 * The only way the app talks to the Bench API. Request and response types come from schema.d.ts, which is
 * generated from the API's pydantic models (`npm run gen:api`); nothing here hand-writes a response shape.
 */
import createClient from "openapi-fetch";
import { ApiError } from "./http";
import type { components, paths } from "./schema";

export const api = createClient<paths>({ baseUrl: "" });

export type Schemas = components["schemas"];
export type Verdict = Schemas["Verdict"];
export type RunSummary = Schemas["RunSummary"];

function reasonFrom(error: unknown, response: Response): string {
  if (error && typeof error === "object") {
    const e = error as { detail?: unknown; reason?: unknown };
    const r = e.detail ?? e.reason;
    if (typeof r === "string") return r;
    if (r !== undefined) return JSON.stringify(r);
  }
  return `${response.status} ${response.statusText}`;
}

/** Unwrap an openapi-fetch result: the data, or an ApiError carrying the API's own reason. */
export async function unwrap<T>(
  pending: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await pending;
  if (!response.ok || data === undefined) throw new ApiError(response.status, reasonFrom(error, response));
  return data;
}
