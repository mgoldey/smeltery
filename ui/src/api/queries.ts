/**
 * The app's shared reads: one key and one fetcher per route, so two pages asking for the same thing share a cache
 * entry and an invalidation of that key reaches both. Call sites spread a factory into `useQuery` and add their own
 * `enabled`, `refetchInterval` and the like.
 *
 * The keys are the ones the app has always used; mutations invalidate them by prefix (`["runs"]`, `["proposals"]`,
 * `["comments", id]`, `["tasks"]`), so changing one silently stops a page from refreshing.
 */
import { queryOptions, useQuery } from "@tanstack/react-query";
import { api, runByRef, unwrap } from "./client";

type Theme = "light" | "dark";

/** `GET /api/runs`, the run store's listing. */
export const runsQuery = () => queryOptions({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });

/** `GET /api/runs/{id}`. A null id keeps its own key and is meant to be paired with `enabled: false`. */
export const runQuery = (id: string | null) =>
  queryOptions({
    queryKey: ["run", id],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}", { params: { path: { run_id: id ?? "" } } })),
  });

export const verdictQuery = (id: string) =>
  queryOptions({
    queryKey: ["verdict", id],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/verdict", { params: { path: { run_id: id } } })),
  });

export const ddeQuery = (id: string) =>
  queryOptions({
    queryKey: ["ddE", id],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/ddE", { params: { path: { run_id: id } } })),
  });

/** One of a run's Plotly figures, drawn for the page's colour scheme. */
export const figureQuery = (id: string, name: string, theme: Theme) =>
  queryOptions({
    queryKey: ["figure", id, name, theme],
    queryFn: () =>
      unwrap(api.GET("/api/runs/{run_id}/figures/{name}", { params: { path: { run_id: id, name }, query: { theme } } })),
  });

export const perPoseQuery = (id: string) =>
  queryOptions({
    queryKey: ["per_pose", id],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/per_pose", { params: { path: { run_id: id } } })),
  });

/** Where candidates were lost per stage, with the generator's rejections when the run had a generation step. */
export const attritionQuery = (id: string) =>
  queryOptions({
    queryKey: ["attrition", id],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/attrition", { params: { path: { run_id: id } } })),
  });

export const droppedPosesQuery = (id: string) =>
  queryOptions({
    queryKey: ["dropped_poses", id],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/dropped_poses", { params: { path: { run_id: id } } })),
  });

/** The proposals filed against a run, by its input digest (what the proposal store keys them on). */
export const proposalsQuery = (digest: string | undefined) =>
  queryOptions({
    queryKey: ["proposals", digest],
    queryFn: () => unwrap(api.GET("/api/proposals", { params: { query: { run: digest ?? "" } } })),
  });

export const commentsQuery = (id: string | undefined) =>
  queryOptions({
    queryKey: ["comments", id],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/comments", { params: { path: { run_id: id ?? "" } } })),
  });

export const tasksQuery = () => queryOptions({ queryKey: ["tasks"], queryFn: () => unwrap(api.GET("/api/tasks")) });

export const whoamiQuery = () => queryOptions({ queryKey: ["whoami"], queryFn: () => unwrap(api.GET("/api/whoami")) });

/**
 * A run's input digest, read out of the run listing: the join proposals need, since they are filed by digest and
 * a page knows only the run's id. `digest` is undefined while the listing loads, when it failed (`runs` says which),
 * or when the run has no digest.
 */
export function useRunDigest(runId: string | undefined) {
  const runs = useQuery({ ...runsQuery(), enabled: runId !== undefined });
  return { digest: runByRef(runs.data, runId)?.digest ?? undefined, runs };
}
