import { existsSync, readFileSync } from "node:fs";
import { expect, newPipeline, openWorkspace, test } from "./bench";

test("Run pipeline is disabled while a blocking issue remains", async ({ page }) => {
  await openWorkspace(page, (await newPipeline(page)).name, "active_site");
  // The run's pocket sites cannot be recovered from its record, so the starting pipeline has no charge yet.
  await expect(page.locator('[data-panel="active_site"] .issue[data-level="error"]').first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Run pipeline" })).toBeDisabled();
});

test("a clean pipeline launches: the task runs smeltery on OUT/config.toml, and the config is that pipeline", async ({ page }) => {
  await openWorkspace(page, (await newPipeline(page)).name, "active_site");
  await page.locator('[data-atom-index="3"]').click();
  await page.locator('[data-atom-index="4"]').click();
  await page.getByRole("button", { name: "Place site" }).click();
  await expect(page.getByTestId("toml-pane")).toContainText("[[pocket.site]]");
  await page.getByRole("region", { name: "Impact" }).getByRole("button", { name: "Keep" }).click();
  const run = page.getByRole("button", { name: "Run pipeline" });
  await expect(run).toBeEnabled({ timeout: 15_000 });
  const toml = (await page.getByTestId("toml-pane").innerText()).trim();
  await run.click();
  await expect(page.getByTestId("launched")).toContainText("Pipeline running");

  // Data source: the task the queue recorded, and the config file on disk.
  const tasks = (await (await page.request.get("/api/tasks")).json()) as { id: string; command: string[]; status: string }[];
  const task = tasks.find((t) => t.command.includes("--out") && t.command.some((c) => c.endsWith("config.toml")))!;
  expect(task).toBeTruthy();
  const out = task.command[task.command.indexOf("--out") + 1]!;
  expect(task.command.slice(-4)).toEqual(["run", `${out}/config.toml`, "--out", out].slice(-4));
  expect(task.command.slice(-5)[0]!.endsWith("smeltery") || task.command.includes("smeltery.cli")).toBe(true);
  expect(existsSync(`${out}/config.toml`)).toBe(true);
  const written = readFileSync(`${out}/config.toml`, "utf8");
  expect(written).toContain("[[pocket.site]]");
  expect(written).toMatch(/atom = 3\b/);
  expect(written.replace(/\s+/g, " ")).toContain(toml.split("\n").filter((l) => l.startsWith("campaign"))[0]!.trim());

  await page.goto("/bench");
  await expect(page.locator(`[data-task="${task.id}"]`)).toBeVisible();
  await page.request.post(`/api/tasks/${task.id}/cancel`); // do not leave a real SCF running after the test
});
