import { useSyncExternalStore } from "react";

/** The colour scheme the page is drawn in. Figures are fetched for it, so Python colours them for the same theme. */
export type Scheme = "light" | "dark";

const QUERY = "(prefers-color-scheme: dark)";

function subscribe(onChange: () => void): () => void {
  const m = window.matchMedia(QUERY);
  m.addEventListener("change", onChange);
  return () => m.removeEventListener("change", onChange);
}

const current = (): Scheme => (window.matchMedia(QUERY).matches ? "dark" : "light");

export function useScheme(): Scheme {
  return useSyncExternalStore(subscribe, current, () => "light");
}
