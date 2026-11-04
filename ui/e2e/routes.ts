import { IDX } from "./bench";

/** Every page of the app, by fixture. A page's feature adds it here so the accessibility checks cover it.
 * Evaluated when tests are collected, so it uses IDX, not .fixture.json (which the server writes later). */
export function routes(): string[] {
  return [
    "/bench",
    `/run/${IDX.a_resolved}/overview`,
    `/run/${IDX.c_incomplete}/overview`,
    ...Object.values(IDX).map((i) => `/run/${i}/results`),
    `/run/${IDX.a_resolved}/poses`,
    `/run/${IDX.e_noposes}/poses`,
    `/run/${IDX.a_resolved}/structure`,
    `/run/${IDX.a_resolved}/pipeline`,
    `/run/${IDX.a_resolved}/decisions`,
    `/run/${IDX.a_resolved}/proposals`,
    "/studies/bench.study_dag_fixture",
    "/compare",
    `/report/${IDX.a_resolved}`,
    `/report/${IDX.c_incomplete}`,
    "/no/such/page",
  ];
}
