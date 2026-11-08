import { expect, newPipeline, openWorkspace, test } from "./bench";

test("clone then rename a pipeline from the workspace toolbar, then re-import its bundle", async ({ page }) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "ligands", { unfoldToml: false });
  const copy = `${name}-c`;
  const renamed = `${name}-r`;

  page.once("dialog", (d) => void d.accept(copy));
  await page.getByRole("button", { name: "Clone…" }).click();
  await expect(page).toHaveURL(new RegExp(`/pipelines/${copy}`));

  page.once("dialog", (d) => void d.accept(renamed));
  await page.getByRole("button", { name: "Rename…" }).click();
  await expect(page).toHaveURL(new RegExp(`/pipelines/${renamed}`));
  const listed = (await (await page.request.get("/api/pipelines")).json()) as string[];
  expect(listed).toContain(name);
  expect(listed).toContain(renamed);
  expect(listed).not.toContain(copy);

  // a taken name is explained, not swallowed
  page.once("dialog", (d) => void d.accept(name));
  await page.getByRole("button", { name: "Clone…" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "already exists" })).toBeVisible();

  const bundle = await (await page.request.get(`/api/pipelines/${name}/bundle`)).body();
  await page.goto("/bench");
  await page.setInputFiles("#import-bundle", { name: "b.zip", mimeType: "application/zip", buffer: bundle });
  await expect(page).toHaveURL(new RegExp(`/pipelines/${name}-2`));
});

test("a bundle with a bad entry is refused and the reason is shown", async ({ page }) => {
  await page.goto("/bench");
  await page.setInputFiles("#import-bundle", { name: "x.zip", mimeType: "application/zip", buffer: Buffer.from("nope") });
  await expect(page.getByRole("alert").filter({ hasText: "not a zip" })).toBeVisible();
});
