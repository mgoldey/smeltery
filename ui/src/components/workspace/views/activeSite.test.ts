import { describe, expect, it } from "vitest";
import { tomlValue } from "./ActiveSiteView";

describe("tomlValue", () => {
  const toml = `campaign = "c"
[structure]
spec = "7LCJ.pdb"   # a path, or a 4-char PDB id
pocket_cutoff = 12.0
[docking]
receptor = 'r.pdbqt'
`;
  it("reads a quoted string from the right table", () => {
    expect(tomlValue(toml, "structure", "spec")).toBe("7LCJ.pdb");
    expect(tomlValue(toml, "docking", "receptor")).toBe("r.pdbqt");
  });
  it("reads a bare number and drops a trailing comment", () => {
    expect(tomlValue(toml, "structure", "pocket_cutoff")).toBe("12.0");
  });
  it("is empty when the table or the key is absent, and never reads another table's key", () => {
    expect(tomlValue(toml, "pocket", "file")).toBe("");
    expect(tomlValue(toml, "structure", "receptor")).toBe("");
    expect(tomlValue("", "structure", "spec")).toBe("");
  });
});
