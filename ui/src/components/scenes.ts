/** MolViewSpec states built in the browser, in the same JSON shape molviewspec (Python) emits. */

type Node = { kind: string; params?: Record<string, unknown>; children?: Node[] };

function state(children: Node[]) {
  return {
    kind: "single",
    root: { kind: "root", children },
    metadata: { version: "1.8", timestamp: new Date().toISOString() },
  };
}

/** A receptor: protein as cartoon; ligands and waters as ball-and-stick, so what preparation drops is visible. */
export function receptorScene(url: string) {
  const component = (selector: string, type: string): Node => ({
    kind: "component",
    params: { selector },
    children: [{ kind: "representation", params: { type } }],
  });
  return state([
    {
      kind: "download",
      params: { url },
      children: [
        {
          kind: "parse",
          params: { format: "pdb" },
          children: [
            {
              kind: "structure",
              params: { type: "model" },
              children: [
                component("polymer", "cartoon"),
                component("polymer", "ball_and_stick"),
                component("ligand", "ball_and_stick"),
                component("water", "ball_and_stick"),
              ],
            },
          ],
        },
      ],
    },
  ]);
}
