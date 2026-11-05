import { describe, expect, it } from "vitest";
import { chosen } from "./ActiveSiteView";

describe("chosen", () => {
  it("reads which of the three ways a draft is written in", () => {
    expect(chosen('[structure]\nspec = "x.pdb"\n')).toBe("structure");
    expect(chosen('[pocket]\nfile = "p.pqr"\n[docking]\n')).toBe("pocket_file");
    expect(chosen("[pocket]\n[[pocket.site]]\natom = 1\n")).toBe("hand_placed");
    expect(chosen('[pocket]\n\n[[pocket.site]]\nq = 1\n\n[x]\nfile = "no"\n')).toBe("hand_placed");
    expect(chosen("")).toBe("hand_placed");
  });
});
