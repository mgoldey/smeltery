import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";

/**
 * What this server allows, from `/api/whoami` (the same query the shell uses).
 *
 * Three states, not two. While the query is in flight the answer is genuinely unknown, and the two plausible
 * defaults are both wrong in one direction: assuming writable flashes Run/Edit controls onto a read-only server
 * (a viewer clicks one and gets a 403), while assuming read-only flashes them *out* of a writable one. So callers
 * get `readOnly` for "known to refuse writes" and `known` for "the server has answered". Hide a write control
 * unless writing is known to be allowed (`!readOnly && known`); say "read-only" in the UI only when `readOnly`.
 */
export function useCapabilities(): { readOnly: boolean; known: boolean } {
  const q = useQuery({
    queryKey: ["whoami"],
    queryFn: () => unwrap(api.GET("/api/whoami")),
  });
  return { readOnly: q.data?.read_only === true, known: q.isSuccess };
}

/** True once the server has answered and said writes are allowed. The predicate a write control should use. */
export function useCanWrite(): boolean {
  const { readOnly, known } = useCapabilities();
  return known && !readOnly;
}
