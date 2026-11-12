/**
 * The only way the app talks to the Bench API. Request and response types come from schema.d.ts, which is
 * generated from the API's pydantic models (`npm run gen:api`); nothing here hand-writes a response shape.
 */
import createClient from "openapi-fetch";
import { ApiError, NETWORK_REASON, describeReason } from "./http";
import type { components, paths } from "./schema";

export const api = createClient<paths>({ baseUrl: "" });

export type Schemas = components["schemas"];
export type Verdict = Schemas["Verdict"];
export type RunSummary = Schemas["RunSummary"];

/**
 * The run a `/run/<ref>` URL names, out of a `GET /api/runs` listing. `ref` is normally a stable id, matched on
 * `id`; an older all-digits link is still its position in that listing, which is what the API does too (B04).
 */
export function runByRef(runs: RunSummary[] | undefined, ref: string | undefined): RunSummary | undefined {
  if (ref === undefined || runs === undefined) return undefined;
  return /^\d+$/.test(ref) ? runs[Number(ref)] : runs.find((r) => r.id === ref);
}

/** Unwrap an openapi-fetch result: the data, or an ApiError carrying the API's own reason. */
export async function unwrap<T>(
  pending: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result;
  try {
    result = await pending;
  } catch (e) {
    if (e instanceof TypeError) throw new ApiError(0, NETWORK_REASON);
    throw e;
  }
  const { data, error, response } = result;
  if (!response.ok || data === undefined)
    throw new ApiError(response.status, describeReason(response.status, response.statusText, error));
  return data;
}
