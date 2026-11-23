import type { Schemas } from "../api/client";

type Info = Schemas["StructureInfo"];

/** Compact validation summary of the opened PDB; anything worth a second look is flagged as a warning. */
function StructureChecks({ info }: { info: Info }) {
  const chains = Object.entries(info.chains ?? {});
  const flags = [
    ...(info.warnings ?? []),
    ...(info.missing_residues ? [`${info.missing_residues} residue(s) listed missing in REMARK 465`] : []),
    ...((info.possible_gaps ?? []).length ? [`possible numbering gaps: ${(info.possible_gaps ?? []).join("; ")}`] : []),
  ];
  return (
    <div data-testid="structure-checks">
      <p className="lede">
        Chains: {chains.map(([c, n]) => `${c} (${n} residues)`).join(", ") || "none"} · {info.n_hetatm ?? 0} HETATM
      </p>
      {flags.length ? (
        <ul role="alert" data-testid="structure-warnings">
          {flags.map((w) => (
            <li key={w}>Warning: {w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * What an opened structure is (source, hash, origin) and what is worth a second look in it (chains, warnings,
 * missing residues, numbering gaps), the same wherever a structure is opened.
 *
 * `compact` is the one-line identity the active-site step shows beside its own controls; the Structure page shows
 * the full provenance table and what preparation drops.
 */
export function StructureSummary({ info, compact = false }: { info: Info; compact?: boolean }) {
  if (compact) {
    return (
      <>
        <p className="lede">
          <span className="mono" data-testid="active-site-sha">
            {info.sha256.slice(0, 12)}
          </span>{" "}
          · {info.origin} · {info.n_atoms} atoms
        </p>
        <StructureChecks info={info} />
      </>
    );
  }
  return (
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
      <StructureChecks info={info} />
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
    </>
  );
}
