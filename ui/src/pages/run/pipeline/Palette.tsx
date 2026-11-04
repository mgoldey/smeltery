import type { Schemas } from "../../../api/client";

type Catalog = Schemas["CatalogOut"];

/**
 * Methods by rung, then by the quantity they measure. Rigour only orders methods that measure the same quantity
 * (Method.comparable_rigour), so methods of different quantities are never put in one ordered list.
 */
export function Palette({
  catalog,
  placed,
  addable,
  onAdd,
  disabled,
}: {
  catalog: Catalog;
  placed: string[];
  addable: string[];
  onAdd: (key: string) => void;
  disabled: boolean;
}) {
  return (
    <nav className="palette" aria-label="Method palette">
      {catalog.rungs.map((rung) => {
        const methods = rung.methods.map((k) => catalog.methods[k]).filter((m): m is Schemas["MethodOut"] => !!m && !m.implicit);
        if (methods.length === 0) return null;
        const byQuantity = new Map<string, Schemas["MethodOut"][]>();
        for (const m of methods) {
          const q = m.quantity ?? "";
          byQuantity.set(q, [...(byQuantity.get(q) ?? []), m]);
        }
        return (
          <section key={rung.name} className="rung" aria-label={`Rung ${rung.name}`}>
            <h3 title={rung.description}>{rung.name.replace("_", " ")}</h3>
            {[...byQuantity.entries()].map(([q, ms]) => (
              <ul key={q} className="quantity-group" data-quantity={q} aria-label={q ? `measures ${q}` : "stages"}>
                {q ? <li className="quantity-label">measures {q}</li> : null}
                {ms.map((m) => {
                  const isPlaced = placed.includes(m.key);
                  return (
                    <li key={m.key} className="method" data-method={m.key}>
                      {m.wired ? (
                        <button type="button" disabled={disabled || isPlaced || !addable.includes(m.key)} onClick={() => onAdd(m.key)} aria-label={`Add ${m.label}`}>
                          {isPlaced ? "Placed" : "Add"}
                        </button>
                      ) : (
                        <span className="unwired-mark">n/a</span>
                      )}
                      <span className="method-label">{m.label}</span>
                      {!m.wired ? <span className="unwired">Not runnable yet: {m.why_unwired}</span> : null}
                    </li>
                  );
                })}
              </ul>
            ))}
          </section>
        );
      })}
    </nav>
  );
}
