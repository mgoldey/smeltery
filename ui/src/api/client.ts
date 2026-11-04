import { ApiError, reasonOf } from "./http";

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new ApiError(res.status, await reasonOf(res));
  return (await res.json()) as T;
}

export interface Verdict {
  state: "RESOLVED" | "UNRANKED" | "INCOMPLETE";
  winner: string | null;
  floor: number | null;
  missing: string[];
}

export interface RunSummary {
  idx: number;
  path: string;
  campaign: string | null;
  parent: string | null;
  digest: string | null;
  n_candidates: number | null;
  verdict: Verdict | null;
  error: string | null;
}
