import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";

/** What this server allows, from `/api/whoami` (the same query the shell uses). Unknown until it answers: writable. */
export function useCapabilities(): { readOnly: boolean } {
  const q = useQuery({ queryKey: ["whoami"], queryFn: () => unwrap(api.GET("/api/whoami")) });
  return { readOnly: q.data?.read_only === true };
}
