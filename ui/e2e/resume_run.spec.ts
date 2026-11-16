/**
 * A run that stopped must offer the way back into itself.
 *
 * The defect: the task inspector's only control was Cancel task, which does nothing once the task is over. The
 * queue reports a dead task failed and never retries it -- re-running is the user's call -- but `smeltery run`
 * resumes a directory it has already partly filled, reading its checkpoint and recomputing only what is missing.
 * The directory holds its own staged config, so it is the whole of what a resume needs; the user was left to read
 * the `--out` path out of the command and re-run it in a shell, or to press Run and pay for the finished SCFs
 * again.
 *
 * This drives the real control against the real route. The task is a command that fails at once, carrying an
 * `--out` the way a run does, so the inspector sees what a died-overnight run leaves behind.
 */
import { expect, fixture, test } from "./bench";

/** Queue a task that fails immediately, and wait for the queue to report it over. */
async function failedTask(
  page: import("@playwright/test").Page,
  name: string,
  command: string[],
) {
  const r = await page.request.post("/e2e/tasks", { data: { name, command } });
  expect(r.status()).toBe(201);
  const { id } = (await r.json()) as { id: string };
  // There is no `GET /api/tasks/{id}` route -- the queue is served as one list -- so find this task in it.
  await expect
    .poll(
      async () => {
        const all = (await (await page.request.get("/api/tasks")).json()) as {
          id: string;
          status: string;
        }[];
        return all.find((x) => x.id === id)?.status;
      },
      { timeout: 20_000 },
    )
    .toBe("failed");
  return id;
}

/** Open a task's inspector the way the panel's own tests do: its row, then Show task. */
async function openInspector(
  page: import("@playwright/test").Page,
  id: string,
) {
  await page.goto("/bench");
  const row = page.locator(`[data-task="${id}"]`);
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row
    .getByRole("button", { name: /Show task/ })
    .first()
    .click();
  return row;
}

test("a failed run offers to resume into the directory it was already writing to", async ({
  page,
}) => {
  // A real run directory on this server: the one holding the first fixture run's record.
  const out = fixture().runs[0]!.path.replace(/\/[^/]+$/, "");
  const id = await failedTask(page, "e2e-resume-me", [
    "false",
    "run",
    `${out}/config.toml`,
    "--out",
    out,
  ]);
  await openInspector(page, id);

  // The control is there, and says what resuming buys rather than reading as a retry.
  const resume = page.getByTestId("resume-run");
  await expect(resume).toBeVisible();
  const note = page.getByTestId("resume-note");
  await expect(note).toContainText(out);
  await expect(note).toContainText("finished");

  // Pressing it reaches the route. This directory holds a finished run record, so the server refuses it as
  // finished -- the right answer, and proof the button is wired to that check rather than to a client-side guess
  // about which directories are resumable.
  await resume.click();
  await expect(
    page.getByText(/already holds the run record|nothing to resume/i),
  ).toBeVisible({ timeout: 15_000 });
});

test("a task that is not a run offers no resume", async ({ page }) => {
  // No `--out`: there is no directory to resume into, so no guess is made at one.
  const id = await failedTask(page, "e2e-not-a-run", ["false"]);
  await openInspector(page, id);
  await expect(page.getByTestId("task-log")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("resume-run")).toHaveCount(0);
});
