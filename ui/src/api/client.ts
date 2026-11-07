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
