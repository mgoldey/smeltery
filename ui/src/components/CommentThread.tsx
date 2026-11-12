import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useCapabilities } from "../api/capabilities";
import { api, unwrap } from "../api/client";

/** Comments anchored to the run, a candidate (`candidate:<name>`) or a pose (`pose:<name>:<i>`). Text is text. */
export function CommentThread({ runId, anchor, title }: { runId: string; anchor: string; title: string }) {
  const qc = useQueryClient();
  const { readOnly } = useCapabilities();
  const all = useQuery({
    queryKey: ["comments", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/comments", { params: { path: { run_id: runId } } })),
  });
  const [body, setBody] = useState("");
  const add = useMutation({
    mutationFn: () => unwrap(api.POST("/api/runs/{run_id}/comments", { params: { path: { run_id: runId } }, body: { anchor, body } })),
    onSuccess: () => {
      setBody("");
      return qc.invalidateQueries({ queryKey: ["comments", runId] });
    },
  });
  const mine = (all.data ?? []).filter((c) => c.anchor === anchor);
  const id = `comment-${anchor.replace(/[^a-zA-Z0-9]/g, "-")}`;
  return (
    <div className="thread" data-thread={anchor}>
      {mine.length ? (
        <ul className="comments">
          {mine.map((c) => (
            <li key={c.id}>
              <span className="comment-author">{c.author}</span> <span className="lede">{c.created_at.replace("T", " ").replace("Z", "")}</span>
              <p>{c.body}</p>
            </li>
          ))}
        </ul>
      ) : null}
      {readOnly ? null : (
      <form
        className="comment-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (body.trim()) add.mutate();
        }}
      >
        <label htmlFor={id}>Comment on {title}</label>
        <textarea id={id} rows={2} value={body} onChange={(e) => setBody(e.target.value)} />
        <button type="submit" disabled={add.isPending || !body.trim()}>
          Add comment
        </button>
        {add.isError ? <span className="field-error">{add.error.message}</span> : null}
      </form>
      )}
    </div>
  );
}
