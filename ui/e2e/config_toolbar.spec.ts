import { readFileSync } from "node:fs";
import { expect, newPipeline, openWorkspace, test } from "./bench";

test("the config is a download, and the raw TOML is folded away until asked for", async ({ page }) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "ligands", { unfoldToml: false });
  await expect(page.getByRole("textbox", { name: "Pipeline TOML" })).toBeHidden();

  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download config.toml" }).click()]);
  expect(dl.suggestedFilename()).toBe(`${name}.config.toml`);
  const text = readFileSync((await dl.path())!, "utf8");
  expect(text).toContain("[candidates]");

  await page.getByText("Edit as TOML").click();
  await expect(page.getByRole("textbox", { name: "Pipeline TOML" })).toContainText("[candidates]");
});

test("the step columns resize by keyboard and the widths survive a reload", async ({ page }) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "measure", { unfoldToml: false });
  const method = page.getByRole("region", { name: "Method" });
  const before = (await method.boundingBox())!.width;
  const divider = page.getByRole("separator", { name: "Resize columns" }).nth(1); // between Method and View
  await divider.focus();
  for (let i = 0; i < 5; i++) await page.keyboard.press("ArrowRight");
  await expect.poll(async () => (await method.boundingBox())!.width).toBeGreaterThan(before + 60);
  const widened = (await method.boundingBox())!.width;

  await page.reload();
  await expect.poll(async () => Math.round((await page.getByRole("region", { name: "Method" }).boundingBox())!.width)).toBe(
    Math.round(widened),
  );
  await page.getByRole("separator", { name: "Resize columns" }).nth(1).dblclick();
  await expect.poll(async () => Math.round((await page.getByRole("region", { name: "Method" }).boundingBox())!.width)).toBe(Math.round(before));
});
