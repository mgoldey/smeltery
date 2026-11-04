import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap, type Schemas } from "../api/client";

type Task = Schemas["Task"];
const LIVE = new Set(["queued", "running"]);

function seconds(from: string | null | undefined, to: string | null | undefined, now: number): number | null {
  if (!from) return null;
  const end = to ? Date.parse(to) : now;
  return Math.max(0, Math.round((end - Date.parse(from)) / 1000));
}

function fmt(s: number | null): string {
  if (s === null) return "";
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min ${s % 60} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

/** The tasks the queue knows, refreshed while any is queued or running. A dead backend degrades this panel only. */
export function WorkPanel({ selected, onSelect }: { selected: string | null; onSelect: (id: string | null) => void }) {
  const [now, setNow] = useState(Date.now());
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: () => unwrap(api.GET("/api/tasks")),
    refetchInterval: (q) => ((q.state.data ?? []).some((t) => LIVE.has(t.status)) ? 1500 : 10_000),
  });
  const live = (tasks.data ?? []).some((t) => LIVE.has(t.status));
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [live]);

  return (
    <section aria-labelledby="work-h">
      <h2 id="work-h">Work</h2>
      <p className="lede">
        A task runs at most once. If its worker dies it is reported failed, not retried: re-running a command is your call.
      </p>
      {tasks.isError ? (
        <div className="banner" role="alert">
          The task queue cannot be reached: {tasks.error.message}. Runs and results still work; this panel will recover
          when the queue does.
        </div>
      ) : tasks.data && tasks.data.length === 0 ? (
        <p className="empty-state">No tasks yet. Launch a run from a pipeline to see it here.</p>
      ) : (
        <table className="grid" aria-label="Tasks">
          <thead>
            <tr>
              <th>Task</th>
              <th>Status</th>
              <th className="num">Elapsed</th>
              <th className="num">Exit code</th>
            </tr>
          </thead>
          <tbody>
            {(tasks.data ?? []).map((t: Task) => (
              <tr key={t.id} className={t.id === selected ? "selected" : undefined} data-task={t.id}>
                <td>
                  <button type="button" className="link" onClick={() => onSelect(t.id)} aria-label={`Show task ${t.name}`}>
                    {t.name}
                  </button>
                </td>
                <td>
                  <span className="status" data-state={t.status}>
                    {t.status}
                  </span>
                </td>
                <td className="num" data-testid="elapsed">
                  {fmt(seconds(t.started_at ?? t.created_at, t.finished_at, now))}
                </td>
                <td className="num">{t.returncode ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** One task: its command, its log tail (as text, never HTML), and cancel while it is live. */
export function TaskInspector({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => unwrap(api.GET("/api/tasks")) });
  const task = tasks.data?.find((t) => t.id === id);
  const log = useQuery({
    queryKey: ["task-log", id],
    queryFn: () => unwrap(api.GET("/api/tasks/{task_id}/log", { params: { path: { task_id: id }, query: { lines: 200 } } })),
    refetchInterval: task && LIVE.has(task.status) ? 1500 : false,
  });
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/tasks/{task_id}/cancel", { params: { path: { task_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  });
  return (
    <aside className="inspector" aria-label="Task">
      <h2 style={{ marginTop: 0 }}>{task?.name ?? id}</h2>
      {task ? (
        <>
          <p className="mono">{task.command.join(" ")}</p>
          <p>
            <span className="status" data-state={task.status}>
              {task.status}
            </span>
            {task.returncode !== null && task.returncode !== undefined ? ` with exit code ${task.returncode}` : ""}
          </p>
          {LIVE.has(task.status) ? (
            <button type="button" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
              Cancel task
            </button>
          ) : null}
          {cancel.isError ? <p className="field-error">{cancel.error.message}</p> : null}
        </>
      ) : null}
      <h3>Log</h3>
      <pre className="log" data-testid="task-log">
        {log.data?.log ?? (log.isError ? log.error.message : "")}
      </pre>
      <button type="button" onClick={onClose}>
        Close
      </button>
    </aside>
  );
}
