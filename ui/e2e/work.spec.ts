import { expect, IDX, newPipeline, test } from "./bench";

test("a running task appears without reloading, its elapsed time advances, and it can be cancelled", async ({ page }) => {
  await page.goto("/bench");
  // Submitted through the API after the page has loaded: it must appear by polling, not by navigation.
  const res = await page.request.post("/api/tasks", {
    data: { name: "e2e-sleeper", command: ["bash", "-c", "printf '<b>bold</b>\\n'; sleep 30"] },
  });
  expect(res.status()).toBe(201);
  const id = ((await res.json()) as { id: string }).id;
  const row = page.locator(`[data-task="${id}"]`);
  await expect(row.locator(".status").first()).toHaveText("running", { timeout: 15_000 });
  const elapsed = row.getByTestId("elapsed");
  const first = await elapsed.innerText();
  await expect.poll(() => elapsed.innerText(), { timeout: 10_000 }).not.toBe(first);

  await row.getByRole("button", { name: "Show task e2e-sleeper" }).click();
  const log = page.getByTestId("task-log");
  await expect(log).toContainText("<b>bold</b>"); // the characters, as text
  await expect(log.locator("b")).toHaveCount(0); // and no <b> element was created from them

  await page.getByRole("button", { name: "Cancel task" }).click();
  await expect(row.locator(".status").first()).toHaveText("cancelled", { timeout: 15_000 });
});

test("with the task queue down, the panel says why and the rest of the app works", async ({ page }) => {
  // The API's own 503-while-runs-answer behaviour is covered in tests/bench; here the browser sees that response.
  await page.route("**/api/tasks", (route) =>
    route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ reason: "temporal is down" }) }),
  );
  await page.goto("/bench");
  await expect(page.getByRole("alert")).toContainText("temporal is down");
  await expect(page.getByRole("table", { name: "Tasks" })).toHaveCount(0);
  // Scoped to the Runs region: role-name matching is a substring, and a pipeline named after the same campaign
  // (an earlier spec's copy) is a link too.
  const campaign = (await (await page.request.get("/api/runs")).json())[0].campaign as string;
  await page.getByRole("region", { name: "Runs" }).getByRole("link", { name: campaign }).first().click();
  await expect(page.getByRole("region", { name: "Verdict" })).toBeVisible();
  void IDX;
});

test("HPC-004: the panel shows how long a task has been silent, and marks it stalled", async ({ page }) => {
  await page.goto("/bench");
  // Prints once, then goes quiet. The fixture server's queue uses a short stall threshold (see serve_fixture.py),
  // so "stalled" is reachable in a test without waiting out the production default of minutes.
  const res = await page.request.post("/api/tasks", {
    data: { name: "e2e-quiet", command: ["bash", "-c", "echo one; sleep 60"] },
  });
  expect(res.status()).toBe(201);
  const id = ((await res.json()) as { id: string }).id;
  const row = page.locator(`[data-task="${id}"]`);
  await expect(row.locator(".status").first()).toHaveText("running", { timeout: 15_000 });

  // The silence column counts up while the task says nothing.
  const silence = row.getByTestId("silence");
  await expect.poll(() => silence.innerText(), { timeout: 20_000 }).not.toBe("");
  await expect(row.getByTestId("stalled")).toBeVisible({ timeout: 25_000 });
  // Stalled is a warning about silence, not a verdict about life: the task is still running.
  await expect(row.locator('.status[data-state="running"]')).toBeVisible();

  await page.request.post(`/api/tasks/${id}/cancel`);
});

test("HPC-002: a task over its memory limit is marked OOM and says how much it used", async ({ page }) => {
  await page.goto("/bench");
  const grow = "python3 -c \"a=[]\nimport time\nwhile True:\n a.append(bytearray(4_000_000)); time.sleep(0.02)\"";
  const res = await page.request.post("/api/tasks", {
    data: { name: "e2e-hog", command: ["bash", "-c", grow], memory_limit_gb: 0.064 },
  });
  expect(res.status()).toBe(201);
  const id = ((await res.json()) as { id: string }).id;
  const row = page.locator(`[data-task="${id}"]`);
  await expect(row.getByTestId("oom")).toBeVisible({ timeout: 30_000 });
  await expect(row.locator('.status[data-state="failed"]')).toBeVisible();
  await expect(row.getByTestId("peak-rss")).toContainText("GB");

  await row.getByRole("button", { name: "Show task e2e-hog" }).click();
  await expect(page.getByTestId("task-health")).toContainText("OOM at");
});

test("B03: a second launched run waits as queued, and cancelling it means it never starts", async ({ page }) => {
  // A launchable pipeline: the starting one has no pocket charge, which blocks Run (see launch.spec.ts).
  const { name } = await newPipeline(page, (t) =>
    `${t}\n[[pocket.site]]\nq = 1.0\natom = 3\nfrom_atom = 4\ndistance = 3.0\n`,
  );
  await page.goto("/bench");
  // Two launches into different OUT folders. The queue runs one launched run at a time, so the second waits.
  const launch = async (out: string) => {
    const r = await page.request.post(`/api/pipelines/${name}/launch`, { data: { out } });
    expect(r.status(), await r.text()).toBe(201);
    return ((await r.json()) as { id: string }).id;
  };
  const first = await launch("out/e2e-q1");
  const second = await launch("out/e2e-q2");
  const row1 = page.locator(`[data-task="${first}"]`);
  const row2 = page.locator(`[data-task="${second}"]`);
  await expect(row1.locator(".status").first()).toHaveText("running", { timeout: 15_000 });
  await expect(row2.locator(".status").first()).toHaveText("queued", { timeout: 15_000 });
  await expect(row2.getByTestId("queued-hint")).toBeVisible();

  // Cancel works on a queued task, from the inspector, and the task never starts.
  await row2.getByRole("button", { name: /Show task/ }).first().click();
  await expect(page.getByTestId("queued-help")).toBeVisible();
  await page.getByRole("button", { name: "Cancel task" }).click();
  await expect(row2.locator(".status").first()).toHaveText("cancelled", { timeout: 15_000 });
  await page.request.post(`/api/tasks/${first}/cancel`); // do not leave a real SCF running
  await expect(row2.locator(".status").first()).toHaveText("cancelled");
});
