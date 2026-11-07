import { expect, IDX, test } from "./bench";

test("a running task appears without reloading, its elapsed time advances, and it can be cancelled", async ({ page }) => {
  await page.goto("/bench");
  // Submitted through the API after the page has loaded: it must appear by polling, not by navigation.
  const res = await page.request.post("/api/tasks", {
    data: { name: "e2e-sleeper", command: ["bash", "-c", "printf '<b>bold</b>\\n'; sleep 30"] },
  });
  expect(res.status()).toBe(201);
  const id = ((await res.json()) as { id: string }).id;
  const row = page.locator(`[data-task="${id}"]`);
  await expect(row.locator(".status")).toHaveText("running", { timeout: 15_000 });
  const elapsed = row.getByTestId("elapsed");
  const first = await elapsed.innerText();
  await expect.poll(() => elapsed.innerText(), { timeout: 10_000 }).not.toBe(first);

  await row.getByRole("button", { name: "Show task e2e-sleeper" }).click();
  const log = page.getByTestId("task-log");
  await expect(log).toContainText("<b>bold</b>"); // the characters, as text
  await expect(log.locator("b")).toHaveCount(0); // and no <b> element was created from them

  await page.getByRole("button", { name: "Cancel task" }).click();
  await expect(row.locator(".status")).toHaveText("cancelled", { timeout: 15_000 });
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
