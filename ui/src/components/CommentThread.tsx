import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useCanWrite } from "../api/capabilities";
import { api, unwrap } from "../api/client";
import { stamp } from "./time";
import { commentsQuery } from "../api/queries";

/** Comments anchored to the run, a candidate (`candidate:<name>`) or a pose (`pose:<name>:<i>`). Text is text. */
export function CommentThread({
  runId,
  anchor,
  title,
}: {
  runId: string;
  anchor: string;
  title: string;
}) {
  const qc = useQueryClient();
  const canWrite = useCanWrite();
  const all = useQuery(commentsQuery(runId));
  const [body, setBody] = useState("");
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/runs/{run_id}/comments", {
          params: { path: { run_id: runId } },
          body: { anchor, body },
        }),
      ),
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
              <span className="comment-author">{c.author}</span>{" "}
              <span className="lede">
                {stamp(c.created_at)}
              </span>
              <p>{c.body}</p>
            </li>
          ))}
        </ul>
      ) : null}
      {canWrite ? (
        <form
          className="comment-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (body.trim()) add.mutate();
          }}
        >
          <label htmlFor={id}>Comment on {title}</label>
          <textarea
            id={id}
            rows={2}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
          <button type="submit" disabled={add.isPending || !body.trim()}>
            Add comment
          </button>
          {add.isError ? (
            <span className="field-error">{add.error.message}</span>
          ) : null}
        </form>
      ) : null}
    </div>
  );
}
