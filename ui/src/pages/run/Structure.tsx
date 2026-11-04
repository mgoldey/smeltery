import { useMutation } from "@tanstack/react-query";
import { useState, type DragEvent, type FormEvent } from "react";
import { api, unwrap, type Schemas } from "../../api/client";
import { MolstarViewer } from "../../components/MolstarViewer";
import { receptorScene } from "../../components/scenes";

type Info = Schemas["StructureInfo"];

export function Structure() {
  const [source, setSource] = useState("");
  const [allowNetwork, setAllowNetwork] = useState(false);
  const [dragging, setDragging] = useState(false);
  const open = useMutation<Info, Error, { source: string } | { name: string; text: string }>({
    mutationFn: (req) =>
      "text" in req
        ? unwrap(api.POST("/api/structure/text", { body: req }))
        : unwrap(api.POST("/api/structure", { body: { source: req.source, allow_network: allowNetwork } })),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (source.trim()) open.mutate({ source: source.trim() });
  };
  const drop = async (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) open.mutate({ name: f.name, text: await f.text() });
  };
  const info = open.data;

  return (
    <div className="body">
      <main className="work" onDragOver={(e) => (e.preventDefault(), setDragging(true))} onDragLeave={() => setDragging(false)} onDrop={drop}>
        <h1>Structure</h1>
        <p className="lede">
          Open the target: a local .pdb path, a PDB id, or a UniProt accession for a predicted structure. You can also drop a
          .pdb file anywhere on this page.
        </p>
        <form className="controls" onSubmit={submit} aria-label="Open a structure">
          <div style={{ flex: "1 1 420px" }}>
            <label htmlFor="structure-source">Path, PDB id or UniProt accession</label>
            <input
              id="structure-source"
              className="mono"
              style={{ width: "100%" }}
              value={source}
              onChange={(e) => setSource(e.target.value)}
              aria-describedby={open.isError ? "structure-error" : undefined}
            />
          </div>
          <div>
            <label htmlFor="allow-network">
              <input id="allow-network" type="checkbox" checked={allowNetwork} onChange={(e) => setAllowNetwork(e.target.checked)} />{" "}
              Allow network
            </label>
          </div>
          <button className="primary" type="submit" disabled={open.isPending}>
            Open structure
          </button>
        </form>
        {open.isError ? (
          <p className="field-error" id="structure-error" role="alert">
            {open.error.message}
          </p>
        ) : null}
        {dragging ? <p className="lede">Drop to open.</p> : null}
        {info ? (
          <>
            <h2>What this structure is</h2>
            <table className="grid" aria-label="Structure provenance">
              <tbody>
                <tr>
                  <th scope="row">Source</th>
                  <td className="mono">{info.source}</td>
                </tr>
                <tr>
                  <th scope="row">sha256</th>
                  <td className="mono" data-testid="structure-sha">
                    {info.sha256}
                  </td>
                </tr>
                <tr>
                  <th scope="row">Origin</th>
                  <td data-testid="structure-origin">
                    {info.origin}
                    {info.license ? `, licence ${info.license}` : ""}
                    {info.mean_plddt !== null && info.mean_plddt !== undefined ? `, mean pLDDT ${info.mean_plddt.toFixed(1)}` : ""}
                  </td>
                </tr>
                <tr>
                  <th scope="row">Atoms</th>
                  <td className="num">{info.n_atoms}</td>
                </tr>
              </tbody>
            </table>
            <h2>What preparation drops</h2>
            {info.dropped.length ? (
              <>
                <p className="lede">
                  Every HETATM residue is removed when the receptor is prepared. Crystal waters are among them; a pose that
                  displaces one is ordinary chemistry, but check nothing here is a cofactor the site needs.
                </p>
                <ul data-testid="structure-dropped">
                  {info.dropped.map((r) => (
                    <li key={r} className="mono">
                      {r}
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="empty-state">No HETATM residues: nothing is dropped.</p>
            )}
            <MolstarViewer state={receptorScene(info.file_url)} label="structure viewer" height={460} />
          </>
        ) : null}
      </main>
    </div>
  );
}
