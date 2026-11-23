/** How a run's cut is put into words, the same on every page that shows it. `group` is the 0-based index the API sends. */

/** "tie group N (no order)" for a group of more than one, else "group N". */
export function groupLabel(group: number, size: number): string {
  return size > 1 ? `tie group ${group + 1} (no order)` : `group ${group + 1}`;
}

/** How many rows each group holds, for `groupLabel`. */
export function groupSizes(rows: readonly { group: number }[]): Map<number, number> {
  const sizes = new Map<number, number>();
  for (const r of rows) sizes.set(r.group, (sizes.get(r.group) ?? 0) + 1);
  return sizes;
}

/** Three states: an unranked run has no survivors, and calling those "not kept" would state a cut outcome the run
 * never reached. */
export function cutOutcome(survivor: boolean | null): "unranked" | "kept" | "not kept" {
  return survivor === null ? "unranked" : survivor ? "kept" : "not kept";
}
