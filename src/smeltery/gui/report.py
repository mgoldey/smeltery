"""One self-contained HTML report of a run, for someone who will not install Python (docs/gui-contract.md section 4).

`write_report(run, path)` writes the page; `main(argv)` is `smeltery-report RUN_OR_OUT [-o report.html]`.

Everything that states a result is static HTML and inline SVG written here: the header, the verdict, the forest
plot, the "resolved?" matrix, the table, the per-pose paired lines and the funnel. They render, and print, with
JavaScript off. Only two things need JavaScript and a network: 2D drawings (RDKit.js MinimalLib) and the 3D pose
viewer (Mol*), both from jsdelivr at exact pinned versions with subresource-integrity hashes. If either cannot load,
the page says so in place of the drawing or the viewer; it never shows a blank box.

The page decides nothing the cut did not. Rows come from `results.ddE_table`, pairwise resolution from
`results.resolved_matrix` (the funnel's own predicate), and the keep boundary is drawn only where the cut placed
one. Inside a tie group the cut gave no order, so rows there are sorted by name, and the page says so. An UNRANKED
cut is a banner naming the tie group the keep boundary fell into, not a ranking.

Every string from the record is untrusted (candidate names are user input). HTML and SVG text is escaped here; the
data the scripts read is JSON in a `<script type="application/json">` block with `<`, `>` and `&` written as
`\\u003c`, `\\u003e` and `\\u0026`, so no record string can close the block; the scripts set text with
`textContent` and never write record strings through `innerHTML`.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sys
import time
from importlib import metadata
from pathlib import Path

#: Exact pinned CDN files. Versions and file paths were checked against the npm registry tarballs, which jsdelivr
#: serves unmodified; the hashes are sha384 of those tarball files.
RDKIT_VERSION = "2026.9.1"
MOLSTAR_VERSION = "5.13.1"
RDKIT_JS = f"https://cdn.jsdelivr.net/npm/@rdkit/rdkit@{RDKIT_VERSION}/dist/RDKit_minimal.js"
RDKIT_WASM = f"https://cdn.jsdelivr.net/npm/@rdkit/rdkit@{RDKIT_VERSION}/dist/RDKit_minimal.wasm"
MOLSTAR_JS = f"https://cdn.jsdelivr.net/npm/molstar@{MOLSTAR_VERSION}/build/viewer/molstar.js"
MOLSTAR_CSS = f"https://cdn.jsdelivr.net/npm/molstar@{MOLSTAR_VERSION}/build/viewer/molstar.css"
SRI = {
    RDKIT_JS: "sha384-gBdna7fxxnpz64Sewln4eBm8+sAiS6ur6q7X0xEum//b7MzSjJGK3ENuAmuRL5Y3",
    MOLSTAR_JS: "sha384-9+z+aIrxLisNnfRcDEuprDk53CvigVeZ0Mj/Ox2zvqpDv/rMAQ7p6sKB6X2Rco9l",
    MOLSTAR_CSS: "sha384-D+V6b5rE0GHUfqRo0agsDJDSHDL0nCHDP4pdeWPVF/BoNpV+CGVWfNxyxyd/lDxN",
}

QUANTITY = "dE_int"
_WITHIN = "no order within group"


def _e(s: object) -> str:
    """Escape for HTML text and attribute values (also valid inside inline SVG)."""
    return html.escape(str(s), quote=True)


def _f(x: float | None, nd: int = 2) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    return f"{x:.{nd}f}"


def _short(s: str, n: int = 22) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def json_for_script(data: object) -> str:
    """JSON safe to place inside `<script type="application/json">`: no `<`, `>` or `&` survives, so no string in
    `data` can close the element or open a comment. JSON.parse reads the `\\u` escapes back unchanged."""
    s = json.dumps(data, ensure_ascii=False, allow_nan=False, default=str)
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# ---------------------------------------------------------------------------------------------------------- data


def _display_rows(rows: list[dict]) -> list[dict]:
    """ddE_table rows in cut order, with each tie group's members sorted by name: the cut gave them no order."""
    order: list[int] = []
    by_group: dict[int, list[dict]] = {}
    for r in rows:
        g = int(r["group"])
        if g not in by_group:
            order.append(g)
            by_group[g] = []
        by_group[g].append(r)
    return [r for g in order for r in sorted(by_group[g], key=lambda r: str(r["candidate"]))]


def _keep_boundary(rows: list[dict], unranked: bool) -> int | None:
    """Row index the keep line is drawn above, or None. Only where the cut placed one: a ranked cut whose
    survivors are a proper, non-empty prefix of the rows."""
    if unranked or not rows or any(r["survivor"] is None for r in rows):
        return None
    k = sum(1 for r in rows if r["survivor"])
    if 0 < k < len(rows) and all(r["survivor"] for r in rows[:k]):
        return k
    return None


def _unranked_reason(groups: list[list[str]], keep: int) -> str:
    before = 0
    for gi, g in enumerate(groups):
        if before < keep < before + len(g):
            names = ", ".join(sorted(map(str, g)))
            return (
                f"keep = {keep} falls inside tie group {gi + 1} ({names}): {before} candidate(s) rank above it and "
                f"the group's {len(g)} members cannot be told apart, so keeping {keep} would mean choosing among "
                "them. The cut keeps whole tie groups or refuses, and here it refused: no survivors were named."
            )
        before += len(g)
    return f"The cut did not place a keep boundary at keep = {keep}: no survivors were named."


def _read_receptor(run, rec: object) -> tuple[str | None, str | None]:
    if not rec:
        return None, None
    try:
        p = Path(rec["file"])
        p = p if p.is_absolute() else run.out_dir / p
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != rec["sha256"]:
            return None, f"receptor {rec['file']}: sha256 does not match the record; not shown"
        return data.decode(), None
    except (OSError, KeyError, TypeError, UnicodeDecodeError) as e:
        return None, f"receptor could not be read ({e}); not shown"


def report_data(run) -> dict:
    """Everything the page shows, as plain data. Raises nothing for missing optional parts (per-pose values, pose
    files, receptor): each absence becomes a note the page displays."""
    from . import results

    rec = run.record
    res = rec.get("results", {})
    rows = _display_rows(results.ddE_table(run))
    names, mat = results.resolved_matrix(run)
    idx = {n: i for i, n in enumerate(names)}
    order = [idx[r["candidate"]] for r in rows]
    matrix = [[bool(mat[i][j]) for j in order] for i in order]

    notes: list[str] = []
    try:
        per_pose = results.per_pose_table(run, QUANTITY)
    except KeyError as e:
        per_pose = []
        notes.append(f"Per-pose values were not recorded ({e.args[0] if e.args else e}); paired lines omitted.")

    poses: dict[str, dict] = {}
    pose_errors: dict[str, str] = {}
    recorded = res.get("poses") or {}
    shown = [r["candidate"] for r in rows]
    for c in dict.fromkeys([run.parent, *shown, *run.analogues]):  # the picker follows the table's order
        if c not in recorded:
            continue
        try:
            poses[c] = {"n": int(recorded[c]["n_poses"]), "sdf": results.pose_sdf(run, c)}
        except (OSError, ValueError, KeyError, UnicodeDecodeError) as e:
            pose_errors[c] = f"{type(e).__name__}: {e}"
    receptor, receptor_note = _read_receptor(run, res.get("receptor"))
    if receptor_note:
        notes.append(receptor_note)

    ferric = rec.get("ferric")
    if isinstance(ferric, dict):
        prov = str(ferric.get("provenance", ""))
        extra = "; ".join(f"{k} {v}" for k, v in ferric.items() if k != "provenance")
        ferric_s = prov + (f" ({extra})" if extra else "") if prov else extra
    else:
        ferric_s = "" if ferric is None else str(ferric)

    return {
        "campaign": run.campaign,
        "parent": run.parent,
        "parent_smiles": run.candidates.get(run.parent, ""),
        "candidates": run.candidates,
        "floor": run.floor,
        "floor_source": run.floor_source,
        "z": run.z,
        "keep": run.keep,
        "unit": (res.get("units") or {}).get(QUANTITY, "kcal/mol"),
        "groups": run.groups,
        "survivors": run.survivors,
        "unranked": run.unranked,
        "unranked_reason": _unranked_reason(run.groups, run.keep) if run.unranked else "",
        "rows": rows,
        "matrix": matrix,
        "keep_boundary": _keep_boundary(rows, run.unranked),
        "per_pose": per_pose,
        "funnel": results.funnel_rows(run),
        "poses_recorded": bool(recorded),
        "poses": poses,
        "pose_errors": pose_errors,
        "field": res.get("field") or [],
        "receptor": receptor,
        "notes": notes,
        "input_digest": rec.get("input_digest", ""),
        "ferric": ferric_s,
        "smeltery_version": rec.get("smeltery_version", ""),
        "started_utc": rec.get("started_utc", ""),
        "report_version": _version(),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _version() -> str:
    try:
        return metadata.version("smeltery")
    except metadata.PackageNotFoundError:
        return "unknown"


# ---------------------------------------------------------------------------------------------------------- svg


def _ticks(lo: float, hi: float, n: int = 6) -> list[float]:
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    t = math.ceil(lo / step) * step
    out = []
    while t <= hi + 1e-9 * step:
        out.append(0.0 if abs(t) < 1e-12 * step else t)
        t += step
    return out


def _fmt_tick(t: float) -> str:
    return f"{t:g}"


def forest_svg(d: dict) -> str:
    """Paired ddE mean ± z·SEM per analogue, in cut order. Tie groups are alternating bands; the keep line is drawn
    only where the cut placed one; a ±floor band sits behind each row when floor > 0."""
    rows, floor, z, unit = d["rows"], d["floor"], d["z"], d["unit"]
    if not rows:
        return '<p class="note">No analogues were measured.</p>'
    left, right, top, rh = 150, 24, 30, 40
    w = 760
    pw = w - left - right
    h = top + rh * len(rows) + 46
    xs = [0.0]
    for r in rows:
        xs += [r["lo"], r["hi"], r["mean"]]
        if floor > 0:
            xs += [r["mean"] - floor, r["mean"] + floor]
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    lo, hi = min(xs), max(xs)
    pad = 0.08 * (hi - lo or 1.0)
    lo, hi = lo - pad, hi + pad

    def X(v: float) -> float:
        return left + (v - lo) / (hi - lo) * pw

    out = [
        f'<svg class="chart forest" viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="Forest plot of paired ddE with {z:g}-sigma intervals">'
    ]
    # tie-group bands
    for k, g in enumerate(dict.fromkeys(r["group"] for r in rows)):
        idx = [i for i, r in enumerate(rows) if r["group"] == g]
        y0, y1 = top + rh * idx[0], top + rh * (idx[-1] + 1)
        cls = "band band-a" if k % 2 == 0 else "band band-b"
        out.append(f'<rect class="{cls}" x="0" y="{y0}" width="{w}" height="{y1 - y0}"/>')
        label = f"group {k + 1}" + (f" · {_WITHIN}" if len(idx) > 1 else "")
        out.append(f'<text class="band-label" x="{w - right}" y="{y0 + 11}" text-anchor="end">{_e(label)}</text>')
    # grid and axis
    y_axis = top + rh * len(rows)
    for t in _ticks(lo, hi):
        x = X(t)
        out.append(f'<line class="grid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{y_axis}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{y_axis + 16}" text-anchor="middle">{_fmt_tick(t)}</text>')
    out.append(f'<line class="zero" x1="{X(0):.1f}" y1="{top - 6}" x2="{X(0):.1f}" y2="{y_axis}"/>')
    out.append(
        f'<text class="tick" x="{X(0):.1f}" y="{top - 10}" text-anchor="middle">'
        f"parent ({_e(_short(d['parent']))})</text>"
    )
    zs = f"{z:g}"
    out.append(
        f'<text class="axis-label" x="{left + pw / 2:.1f}" y="{y_axis + 36}" text-anchor="middle">'
        f"paired ΔΔE vs parent ({_e(unit)}); bars: mean ± {zs}·SEM</text>"
    )
    for i, r in enumerate(rows):
        yc = top + rh * i + rh / 2 + 4
        tip = (
            f"{r['candidate']}: {_f(r['mean'])} ± {_f(r['sem'])} {unit} (SEM, n={r['n']}); "
            f"interval [{_f(r['lo'])}, {_f(r['hi'])}]"
        )
        out.append(f'<g class="row"><title>{_e(tip)}</title>')
        out.append(f'<rect class="hit" x="0" y="{top + rh * i}" width="{w}" height="{rh}"/>')
        cls = "name survivor" if r["survivor"] else "name"
        out.append(
            f'<text class="{cls}" x="{left - 10}" y="{yc + 4:.1f}" text-anchor="end">'
            f"{_e(_short(r['candidate']))}</text>"
        )
        if floor > 0:
            a, b = X(r["mean"] - floor), X(r["mean"] + floor)
            out.append(f'<rect class="floor" x="{a:.1f}" y="{yc - 7:.1f}" width="{b - a:.1f}" height="14" rx="3"/>')
        if math.isfinite(r["lo"]) and math.isfinite(r["hi"]):
            a, b = X(r["lo"]), X(r["hi"])
            out.append(f'<line class="whisker" x1="{a:.1f}" y1="{yc:.1f}" x2="{b:.1f}" y2="{yc:.1f}"/>')
            for x in (a, b):
                out.append(f'<line class="whisker" x1="{x:.1f}" y1="{yc - 5:.1f}" x2="{x:.1f}" y2="{yc + 5:.1f}"/>')
        mcls = "mean survivor" if r["survivor"] else "mean"
        out.append(f'<circle class="{mcls}" cx="{X(r["mean"]):.1f}" cy="{yc:.1f}" r="5"/></g>')
    kb = d["keep_boundary"]
    if kb is not None:
        y = top + rh * kb
        out.append(f'<line class="keep" x1="0" y1="{y}" x2="{w}" y2="{y}"/>')
        out.append(f'<text class="keep-label" x="6" y="{y + 12}">keep = {_e(d["keep"])}: survivors above</text>')
    out.append("</svg>")
    return "".join(out)


def perpose_svgs(d: dict) -> str:
    """One small slope chart per analogue: each pose's parent value joined to the analogue's. Shared y scale."""
    pp = d["per_pose"]
    if not pp:
        return ""
    vals = [v for r in pp for v in (r["value"], r["parent_value"]) if math.isfinite(v)]
    lo, hi = min(vals), max(vals)
    pad = 0.08 * (hi - lo or 1.0)
    lo, hi = lo - pad, hi + pad
    w, h, left, top, bottom = 240, 200, 52, 14, 34
    ph = h - top - bottom
    x0, x1 = left + 24, w - 30

    def Y(v: float) -> float:
        return top + (hi - v) / (hi - lo) * ph

    by: dict[str, list[dict]] = {}
    for r in pp:
        by.setdefault(r["candidate"], []).append(r)
    rowinfo = {r["candidate"]: r for r in d["rows"]}
    order = [r["candidate"] for r in d["rows"] if r["candidate"] in by] + [c for c in by if c not in rowinfo]
    out = []
    for c in order:
        s = [f'<figure class="pp"><svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{_e(c)} per pose">']
        for t in _ticks(lo, hi, 4):
            y = Y(t)
            s.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{w - 10}" y2="{y:.1f}"/>')
            s.append(f'<text class="tick" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt_tick(t)}</text>')
        for r in by[c]:
            a, b = Y(r["parent_value"]), Y(r["value"])
            tip = f"pose {r['pose']}: parent {_f(r['parent_value'])}, {c} {_f(r['value'])}, paired {_f(r['paired'])}"
            s.append(f"<g><title>{_e(tip)}</title>")
            s.append(f'<line class="slope" x1="{x0}" y1="{a:.1f}" x2="{x1}" y2="{b:.1f}"/>')
            s.append(f'<circle class="pt-parent" cx="{x0}" cy="{a:.1f}" r="4"/>')
            s.append(f'<circle class="pt" cx="{x1}" cy="{b:.1f}" r="4"/></g>')
        s.append(f'<text class="tick" x="{x0}" y="{h - 16}" text-anchor="middle">parent</text>')
        s.append(f'<text class="tick" x="{x1}" y="{h - 16}" text-anchor="middle">{_e(_short(c, 14))}</text>')
        s.append("</svg>")
        info = rowinfo.get(c)
        cap = f"<b>{_e(c)}</b>"
        if info:
            cap += f"<br>paired SEM {_e(_f(info['sem']))} · unpaired SEM {_e(_f(info['unpaired_sem']))} {_e(d['unit'])}"
        s.append(f"<figcaption>{cap}</figcaption></figure>")
        out.append("".join(s))
    return "".join(out)


# ---------------------------------------------------------------------------------------------------------- html


def _matrix_html(d: dict) -> str:
    rows, mat = d["rows"], d["matrix"]
    if not rows:
        return ""
    starts = {i for i, r in enumerate(rows) if i == 0 or rows[i - 1]["group"] != r["group"]}
    head = "".join(
        f'<th scope="col" class="{"gs" if j in starts else ""}" title="{_e(r["candidate"])}">'
        f"<span>{_e(_short(r['candidate'], 14))}</span></th>"
        for j, r in enumerate(rows)
    )
    body = []
    for i, r in enumerate(rows):
        cells = []
        for j in range(len(rows)):
            cls = ["gs"] if j in starts else []
            same = rows[j]["group"] == r["group"]
            if same:
                cls.append("same")
            if i == j:
                txt, t = "—", "itself"
            elif mat[i][j]:
                txt, t = "≠", "resolved"
                cls.append("res")
            else:
                txt, t = "≈", "not resolved"
            tip = f"{r['candidate']} vs {rows[j]['candidate']}: {t}"
            cells.append(f'<td class="{" ".join(cls)}" title="{_e(tip)}">{txt}</td>')
        tr = ' class="gs"' if i in starts else ""
        body.append(f'<tr{tr}><th scope="row">{_e(_short(r["candidate"]))}</th>{"".join(cells)}</tr>')
    return (
        f'<div class="scroll"><table class="matrix"><thead><tr><th></th>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def _table_html(d: dict) -> str:
    unit = _e(d["unit"])
    out = [
        '<div class="scroll"><table class="cands"><thead><tr><th>Structure</th><th>Name</th><th>SMILES</th>'
        f"<th>ΔΔE mean ± SEM ({unit})</th><th>Unpaired SEM</th><th>n</th><th>Group</th>"
        "<th>Survivor</th></tr></thead><tbody>"
    ]
    gnum = {g: k + 1 for k, g in enumerate(dict.fromkeys(r["group"] for r in d["rows"]))}
    gsize = {g: sum(1 for r in d["rows"] if r["group"] == g) for g in gnum}
    for k, r in enumerate(d["rows"]):
        surv = {True: "✓ kept", False: "no", None: "unranked"}[r["survivor"]]
        gtxt = f"{gnum[r['group']]}" + (f" ({_WITHIN})" if gsize[r["group"]] > 1 else "")
        out.append(
            f'<tr><td><div class="mol2d" data-k="{k}">2D drawing needs RDKit.js</div></td>'
            f'<td>{_e(r["candidate"])}</td><td class="smi">{_e(r["smiles"])}</td>'
            f'<td class="num">{_f(r["mean"])} ± {_f(r["sem"])}</td>'
            f'<td class="num">{_f(r["unpaired_sem"])}</td><td class="num">{_e(r["n"])}</td>'
            f'<td>{_e(gtxt)}</td><td class="{"surv" if r["survivor"] else ""}">{surv}</td></tr>'
        )
    out.append("</tbody></table></div>")
    return "".join(out)


def _funnel_html(d: dict) -> str:
    rows = d["funnel"]
    if not rows:
        return '<p class="note">The record holds no funnel counts.</p>'
    mx = max([int(r.get("candidates_in") or 0) for r in rows] + [1])
    out = [
        '<table class="funnel"><thead><tr><th>Stage</th><th>Kind</th><th>In</th><th>Out</th><th></th></tr>'
        "</thead><tbody>"
    ]
    for r in rows:
        n_in, n_out = r.get("candidates_in"), r.get("candidates_out")
        if n_out is None:
            out_s, frac = ("UNRANKED" if r.get("unranked") else "—"), 0
        else:
            out_s, frac = str(n_out), int(n_out) / mx
        out.append(
            f"<tr><td>{_e(r.get('stage', ''))}</td><td>{_e(r.get('kind', ''))}</td>"
            f'<td class="num">{_e("" if n_in is None else n_in)}</td><td class="num">{_e(out_s)}</td>'
            f'<td class="barcell"><span class="bar" style="width:{100 * frac:.0f}%"></span></td></tr>'
        )
    out.append("</tbody></table>")
    return "".join(out)


def _header_html(d: dict) -> str:
    items = [
        ("Campaign", d["campaign"]),
        ("Parent", f"{d['parent']}  ({d['parent_smiles']})"),
        ("Floor", f"{d['floor']:g} {d['unit']}"),
        ("Floor source", d["floor_source"]),
        ("z", f"{d['z']:g}"),
        ("Keep", d["keep"]),
        ("Input digest", d["input_digest"]),
        ("ferric", d["ferric"]),
        ("smeltery (run)", d["smeltery_version"]),
        ("Run started", d["started_utc"]),
        ("Report written", f"{d['generated_utc']} by smeltery {d['report_version']}"),
    ]
    dl = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in items)
    return f'<dl class="meta">{dl}</dl>'


def _verdict_html(d: dict) -> str:
    if d["unranked"]:
        return (
            '<div class="verdict unranked" role="alert"><strong>UNRANKED</strong>'
            f"<p>{_e(d['unranked_reason'])}</p></div>"
        )
    s = d["survivors"] or []
    names = ", ".join(s) if s else "none"
    return (
        f'<div class="verdict ranked"><strong>Survivors (keep = {_e(d["keep"])}):</strong> {_e(names)}'
        "<p>The cut separated these from the rest at the given z and floor. Tie groups are kept or dropped whole.</p>"
        "</div>"
    )


def _viewer_html(d: dict) -> str:
    if not d["poses_recorded"]:
        return (
            '<p class="note" id="viewer-msg">Pose files were not recorded in this run, so there is no 3D view. '
            "Everything else in this report is complete.</p>"
        )
    errs = "".join(f"<li>{_e(c)}: {_e(m)}</li>" for c, m in d["pose_errors"].items())
    errs = f'<p class="note">Some pose files could not be read and are left out:</p><ul>{errs}</ul>' if errs else ""
    return f"""{errs}
<div class="controls">
  <label>Candidate <select id="pv-cand"></select></label>
  <label>Pose <select id="pv-pose"></select></label>
  <label><input type="checkbox" id="pv-parent" checked> overlay parent pose (grey)</label>
  <label><input type="checkbox" id="pv-field" checked> field charges (blue +, red −)</label>
  <a id="pv-dl" href="#" download>Download this candidate's SDF</a>
</div>
<p class="note" id="viewer-msg" aria-live="polite">The 3D viewer needs JavaScript and Mol* {MOLSTAR_VERSION} from
cdn.jsdelivr.net.</p>
<div id="molstar" class="viewer"></div>"""


_CSS = """
:root{color-scheme:light;--bg:#fcfcfb;--card:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--line:#e4e3df;
--band-a:#f3f2ee;--band-b:#ffffff;--accent:#2a78d6;--warn-bg:#fff1e8;--warn-ink:#8a2c00;--warn-line:#eb6834;
--floor:#2a78d633;--pos:#2a78d6;--neg:#e34948;--ok-bg:#eef5fd}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#1a1a19;--card:#222220;
--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e86;--line:#3a3936;--band-a:#2b2b29;--band-b:#222220;--accent:#3987e5;
--warn-bg:#3a2216;--warn-ink:#ffb48f;--warn-line:#d95926;--floor:#3987e544;--pos:#3987e5;--neg:#e66767;
--ok-bg:#1d2a3a}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#1a1a19;--card:#222220;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e86;
--line:#3a3936;--band-a:#2b2b29;--band-b:#222220;--accent:#3987e5;--warn-bg:#3a2216;--warn-ink:#ffb48f;
--warn-line:#d95926;--floor:#3987e544;--pos:#3987e5;--neg:#e66767;--ok-bg:#1d2a3a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1000px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:.5rem 0 1rem}h2{font-size:1.15rem;margin:0 0 .5rem}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 16px}
.note{color:var(--ink2);font-size:.9rem;overflow-wrap:anywhere}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:0}
.meta dt{color:var(--ink2)}.meta dd{margin:0;overflow-wrap:anywhere}
.verdict{border-radius:10px;padding:12px 16px;margin:0 0 16px;border:1px solid var(--line);background:var(--ok-bg)}
.verdict p{margin:.3rem 0 0;color:var(--ink2)}
.verdict.unranked{background:var(--warn-bg);border:2px solid var(--warn-line);color:var(--warn-ink)}
.verdict.unranked strong{font-size:1.4rem;letter-spacing:.06em}.verdict.unranked p{color:var(--warn-ink)}
svg.chart{width:100%;height:auto;display:block;font-family:inherit}
svg .band-a{fill:var(--band-a)}svg .band-b{fill:var(--band-b)}
svg .band-label{fill:var(--muted);font-size:10px}
svg .grid{stroke:var(--line);stroke-width:1}svg .zero{stroke:var(--ink2);stroke-width:1;stroke-dasharray:3 3}
svg .tick{fill:var(--ink2);font-size:11px}svg .axis-label{fill:var(--ink2);font-size:12px}
svg .name{fill:var(--ink);font-size:13px}svg .name.survivor{font-weight:700}
svg .whisker{stroke:var(--ink);stroke-width:2;stroke-linecap:round}
svg .mean{fill:var(--card);stroke:var(--ink);stroke-width:2}svg .mean.survivor{fill:var(--accent);stroke:var(--accent)}
svg .floor{fill:var(--floor)}svg .hit{fill:transparent}svg .row:hover .hit{fill:var(--floor)}
svg .keep{stroke:var(--accent);stroke-width:2}svg .keep-label{fill:var(--accent);font-size:11px;font-weight:600}
svg .slope{stroke:var(--ink2);stroke-width:2}svg .pt{fill:var(--accent);stroke:var(--card);stroke-width:2}
svg .pt-parent{fill:var(--muted);stroke:var(--card);stroke-width:2}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:.85rem;color:var(--ink2);margin:.5rem 0 0}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:4px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;font-size:.9rem}th,td{padding:4px 8px;text-align:left;vertical-align:middle}
.cands td,.cands th,.funnel td,.funnel th{border-bottom:1px solid var(--line)}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.smi{font-family:ui-monospace,monospace;font-size:.8rem;overflow-wrap:anywhere;max-width:16rem}
.surv{color:var(--accent);font-weight:600}
.mol2d{width:140px;height:105px;display:flex;align-items:center;justify-content:center;font-size:.7rem;
color:#6b6a65;text-align:center;background:#fff;border-radius:6px;padding:4px}
.mol2d img{max-width:100%;max-height:100%}
.matrix td{text-align:center;width:2.2em;border:1px solid var(--line);color:var(--muted)}
.matrix td.res{color:var(--ink);font-weight:700}.matrix td.same{background:var(--band-a)}
.matrix td.gs{border-left:2px solid var(--ink2)}.matrix tr.gs td{border-top:2px solid var(--ink2)}
.matrix thead th span{writing-mode:vertical-rl;transform:rotate(180deg);white-space:nowrap}
.pps{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
figure.pp{margin:0 auto;width:100%;max-width:300px}svg.forest{min-width:600px}
figure.pp figcaption{font-size:.8rem;color:var(--ink2);text-align:center}
.funnel .barcell{width:40%}.bar{display:block;height:10px;border-radius:0 4px 4px 0;background:var(--accent)}
.controls{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin:0 0 8px}
.controls select{font:inherit}a{color:var(--accent)}
.viewer{position:relative;width:100%;height:440px;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.viewer.failed{display:none}
@media (max-width:600px){main{padding:8px 16px}section{padding:12px}.meta{grid-template-columns:1fr}
.meta dt{margin-top:6px}.viewer{height:320px}}
@media print{body{background:#fff;color:#000}section{break-inside:avoid;border-color:#ccc}
#pose-section{display:none}.print-only{display:block!important}}
.print-only{display:none}
"""

_JS = r"""
(function(){
'use strict';
const D = JSON.parse(document.getElementById('smeltery-data').textContent);
const CDN = JSON.parse(document.getElementById('smeltery-cdn').textContent);
function loadScript(url){return new Promise(function(ok,fail){
  const s=document.createElement('script');s.src=url;s.crossOrigin='anonymous';
  if(CDN.sri[url])s.integrity=CDN.sri[url];
  s.onload=ok;s.onerror=function(){fail(new Error('could not load '+url))};document.head.appendChild(s);});}
function loadCss(url){const l=document.createElement('link');l.rel='stylesheet';l.href=url;l.crossOrigin='anonymous';
  if(CDN.sri[url])l.integrity=CDN.sri[url];document.head.appendChild(l);}
function withTimeout(p,ms,what){return Promise.race([p,new Promise(function(_,f){
  setTimeout(function(){f(new Error(what+' timed out'))},ms)})]);}

// ---- 2D structures (RDKit.js). Output goes into an <img>, so no SVG text is ever parsed into this document.
const cells=document.querySelectorAll('.mol2d');
function mark2d(msg){cells.forEach(function(c){c.textContent=msg;});}
if(cells.length){
  mark2d('loading 2D drawing…');
  withTimeout(loadScript(CDN.rdkit_js).then(function(){
    return window.initRDKitModule({locateFile:function(){return CDN.rdkit_wasm;}});
  }),30000,'RDKit.js').then(function(RDKit){
    cells.forEach(function(c){
      const m=D.mols[+c.dataset.k]||['',''];const name=m[0], smi=m[1];
      const mol=smi?RDKit.get_mol(smi):null;
      if(!mol){c.textContent='RDKit.js could not parse this SMILES';return;}
      try{const svg=mol.get_svg(240,180);const img=document.createElement('img');
        img.alt='2D structure of '+name;img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg);
        c.textContent='';c.appendChild(img);}finally{mol.delete();}
    });
  }).catch(function(e){mark2d('2D drawing unavailable: RDKit.js could not load');
    cells.forEach(function(c){c.title='RDKit.js '+CDN.rdkit_version+': '+e.message;});});
}

// ---- 3D pose viewer (Mol*).
const box=document.getElementById('molstar');
if(!box)return;
const msg=document.getElementById('viewer-msg');
const cand=document.getElementById('pv-cand'), pose=document.getElementById('pv-pose');
const withParent=document.getElementById('pv-parent'), withField=document.getElementById('pv-field');
const dl=document.getElementById('pv-dl');
const P=D.poses, posed=P.map(function(p){return p.name;});
function byName(c){return P[posed.indexOf(c)];}
function say(t){msg.textContent=t;}
function fail(e){box.classList.add('failed');
  say('The 3D viewer could not load (Mol* '+CDN.molstar_version+' from cdn.jsdelivr.net: '+e.message+'). '+
      'Everything else in this report is static and complete; use "Download this candidate’s SDF" to open '+
      'the poses in PyMOL, Maestro or DataWarrior.');}
posed.forEach(function(c,k){const o=document.createElement('option');o.value=String(k);
  o.textContent=c+(c===D.parent?' (parent)':'');cand.appendChild(o);});
const firstAnalogue=posed.findIndex(function(c){return c!==D.parent;});
if(firstAnalogue>=0)cand.value=String(firstAnalogue);
function cur(){return P[+cand.value];}
function fillPoses(){const n=(cur()||{n:0}).n;const keep=+pose.value||0;pose.textContent='';
  for(let i=0;i<n;i++){const o=document.createElement('option');o.value=String(i);o.textContent=String(i);
    pose.appendChild(o);}
  pose.value=String(Math.min(keep,Math.max(n-1,0)));}
function record(sdf,i){const recs=sdf.split(/\$\$\$\$\r?\n/);return (recs[i]||'')+'$$$$\n';}
let url=null;
function setDownload(){const c=cur();if(!c)return;if(url)URL.revokeObjectURL(url);
  url=URL.createObjectURL(new Blob([c.sdf],{type:'chemical/x-mdl-sdfile'}));dl.href=url;
  dl.setAttribute('download',c.name.replace(/[^A-Za-z0-9._-]/g,'_')+'.sdf');}
if(!posed.length){fail(new Error('no readable pose files'));return;}
fillPoses();setDownload();
function chargesXyz(sign){const pts=D.field.filter(function(p){return sign>0?p[0]>0:p[0]<0;});
  if(!pts.length)return null;
  return pts.length+'\nfield charges\n'+pts.map(function(p){return 'X '+p[1]+' '+p[2]+' '+p[3];}).join('\n')+'\n';}
let viewer=null, busy=Promise.resolve();
async function add(text,fmt,label,color){
  const p=viewer.plugin;
  const data=await p.builders.data.rawData({data:text,label:label});
  const traj=await p.builders.structure.parseTrajectory(data,fmt);
  const opts=color==null?undefined:
    {representationPresetParams:{theme:{globalName:'uniform',globalColorParams:{value:color}}}};
  await p.builders.structure.hierarchy.applyPreset(traj,'default',opts);}
async function show(){
  const cc=cur(), c=cc.name, i=+pose.value, par=byName(D.parent);
  await viewer.plugin.clear();
  if(D.receptor)await add(D.receptor,'pdb','receptor');
  if(withParent.checked&&c!==D.parent&&par)
    await add(record(par.sdf,i),'sdf','parent pose '+i,0x9a9a9a);
  await add(record(cc.sdf,i),'sdf',c+' pose '+i);
  if(withField.checked){const pos=chargesXyz(1),neg=chargesXyz(-1);
    if(pos)await add(pos,'xyz','positive charges',0x2a78d6);if(neg)await add(neg,'xyz','negative charges',0xe34948);}
  say(c+', pose '+i+(withParent.checked&&c!==D.parent?' with the parent’s pose '+i+' in grey':'')+'.');}
function refresh(){busy=busy.then(show).catch(function(e){say('Could not show this pose: '+e.message);});}
cand.addEventListener('change',function(){fillPoses();setDownload();refresh();});
[pose,withParent,withField].forEach(function(el){el.addEventListener('change',refresh);});
say('Loading Mol* '+CDN.molstar_version+'…');
loadCss(CDN.molstar_css);
withTimeout(loadScript(CDN.molstar_js),30000,'Mol*').then(function(){
  return window.molstar.Viewer.create(box,{layoutIsExpanded:false,layoutShowControls:false,layoutShowRemoteState:false,
    layoutShowSequence:false,layoutShowLog:false,layoutShowLeftPanel:false,viewportShowExpand:true,
    viewportShowSelectionMode:false,viewportShowAnimation:false});
}).then(function(v){viewer=v;refresh();}).catch(fail);
})();
"""


def render_html(run) -> str:
    """The report page as a string. See the module docstring for what it shows and refuses to show."""
    d = report_data(run)
    cdn = {
        "rdkit_js": RDKIT_JS,
        "rdkit_wasm": RDKIT_WASM,
        "rdkit_version": RDKIT_VERSION,
        "molstar_js": MOLSTAR_JS,
        "molstar_css": MOLSTAR_CSS,
        "molstar_version": MOLSTAR_VERSION,
        "sri": SRI,
    }
    # Lists, not objects keyed by name: JS orders integer-like keys first, and names are the user's.
    js_data = {
        "parent": d["parent"],
        "mols": [[r["candidate"], r["smiles"]] for r in d["rows"]],
        "poses": [{"name": c, "n": v["n"], "sdf": v["sdf"]} for c, v in d["poses"].items()],
        "field": d["field"],
        "receptor": d["receptor"],
    }
    floor_legend = (
        f'<span><span class="sw" style="background:var(--floor)"></span>±floor ({d["floor"]:g} '
        f"{_e(d['unit'])}): a systematic bound the SEM cannot see</span>"
        if d["floor"] > 0
        else "<span>floor = 0: no systematic band drawn</span>"
    )
    keep_legend = (
        '<span><span class="sw" style="background:var(--accent)"></span>keep line: where the cut placed it</span>'
        if d["keep_boundary"] is not None
        else "<span>no keep line: "
        + ("the cut was UNRANKED" if d["unranked"] else "the cut placed no boundary between rows")
        + "</span>"
    )
    notes = "".join(f'<p class="note">{_e(n)}</p>' for n in d["notes"])
    pp = perpose_svgs(d)
    pp_section = (
        f"<section><h2>Per-pose paired lines ({_e(QUANTITY)}, {_e(d['unit'])})</h2>"
        "<p class=\"note\">Each line joins the parent's pose <i>i</i> to the analogue's pose <i>i</i>, which is built "
        "on it. Parallel lines are why pairing cancels pose noise: compare the paired and unpaired SEM under each "
        f'panel.</p><div class="pps">{pp}</div></section>'
        if pp
        else ""
    )
    title = f"smeltery report: {d['campaign']}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<h1>{_e(title)}</h1>
{_verdict_html(d)}
<section><h2>Run</h2>{_header_html(d)}{notes}</section>
<section><h2>Paired ΔΔE by analogue</h2>
<p class="note">Rows are in the cut's order. Shaded bands are tie groups: the cut could not separate their members,
so inside a band rows are sorted by name and the order means nothing.</p>
<div class="scroll">{forest_svg(d)}</div>
<div class="legend"><span><span class="sw" style="background:var(--band-a)"></span>tie group ({_WITHIN})</span>
{floor_legend}{keep_legend}</div>
</section>
<section><h2>Resolved?</h2>
<p class="note">≠: the funnel's own test (|Δmean| &gt; z·√(SEM₁²+SEM₂²) and
&gt; floor) separates the pair. ≈: it does not. Tie groups are the shaded blocks on the diagonal.</p>
{_matrix_html(d)}
</section>
<section><h2>Candidates</h2>
<p class="note">Cut order; within a tie group, by name ({_WITHIN}). Parent: {_e(d["parent"])}.</p>
{_table_html(d)}
</section>
{pp_section}
<section><h2>Funnel</h2>{_funnel_html(d)}</section>
<section id="pose-section"><h2>Poses</h2>{_viewer_html(d)}</section>
<p class="print-only note">The interactive pose viewer is left out of the printed report.</p>
</main>
<script type="application/json" id="smeltery-data">{json_for_script(js_data)}</script>
<script type="application/json" id="smeltery-cdn">{json_for_script(cdn)}</script>
<script>{_JS}</script>
</body>
</html>
"""


def write_report(run, path: str | Path) -> Path:
    """Write the self-contained HTML report of `run` (a `smeltery.gui.results.Run`) to `path`; return the path.

    Raises what `smeltery.gui.results` raises for a record it cannot read. Missing per-pose values, pose files or a
    receptor do not raise: the report says each was not recorded (or did not verify) and shows everything else."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html(run), encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    """`smeltery-report RUN_OR_OUT [-o report.html]`. Exit 0 on success, 2 when the run cannot be loaded."""
    from . import results

    ap = argparse.ArgumentParser(prog="smeltery-report", description="Write a self-contained HTML report of a run.")
    ap.add_argument("run", type=Path, help="a run-*.json, or an OUT directory holding exactly one")
    ap.add_argument("-o", "--output", type=Path, default=Path("report.html"), help="HTML file to write")
    args = ap.parse_args(argv)
    try:
        run = results.load_run(args.run)
    except (OSError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = write_report(run, args.output)
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
