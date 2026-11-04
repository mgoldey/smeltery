import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, fixture, test } from "./bench";
import { distinctColours } from "./pixels";

test("opening a local PDB shows its hash, origin, what is dropped, and draws it", async ({ page }) => {
  const path = fixture().pdb;
  // Data source: the file itself, hashed and read here, independently of the API.
  const text = readFileSync(path, "utf8");
  const sha = createHash("sha256").update(text).digest("hex");
  const het = [...new Set(text.split("\n").filter((l) => l.startsWith("HETATM")).map((l) => l.slice(17, 21).trim()))];
  await page.goto("/run/0/structure");
  await page.getByLabel("Path, PDB id or UniProt accession").fill(path);
  await page.getByRole("button", { name: "Open structure" }).click();
  await expect(page.getByTestId("structure-sha")).toHaveText(sha);
  await expect(page.getByTestId("structure-origin")).toContainText(text.includes("EXPDTA") ? "EXPERIMENTAL" : "UNKNOWN");
  for (const r of het) await expect(page.getByTestId("structure-dropped")).toContainText(r);
  const viewer = page.locator('[data-viewer="structure viewer"]');
  await expect.poll(async () => Number(await viewer.getAttribute("data-loaded")), { timeout: 30_000 }).toBeGreaterThanOrEqual(1);
  await expect.poll(() => distinctColours(viewer), { timeout: 30_000 }).toBeGreaterThan(20);
});

test("a missing path is reported inline with the API's message", async ({ page }) => {
  await page.goto("/run/0/structure");
  await page.getByLabel("Path, PDB id or UniProt accession").fill("/nowhere/absent.pdb");
  await page.getByRole("button", { name: "Open structure" }).click();
  await expect(page.locator("#structure-error")).toContainText("absent.pdb");
});

test("a PDB id without network permission is refused, saying why", async ({ page }) => {
  await page.goto("/run/0/structure");
  await page.getByLabel("Path, PDB id or UniProt accession").fill("1ABC");
  await page.getByRole("button", { name: "Open structure" }).click();
  await expect(page.locator("#structure-error")).toContainText("network");
});
