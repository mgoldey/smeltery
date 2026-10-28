# GUI helpers: the method hierarchy as a pipeline

`smeltery.gui` is the layer under a pipeline builder. It does not import a GUI toolkit. A frontend (a notebook
widget, a Qt app or a web page reading `catalog_json()`) draws what it returns and calls it back.

## The model

A run is a pipeline of **stages**. Each stage is one **method** from the catalog. A method has:

- a **rung** on the method hierarchy, cheapest first: `input` → `geometry` → `check` → `empirical` →
  `semiempirical` → `ab_initio` → `embedded_qm` → `decision`. `hierarchy()` returns the methods grouped by rung, which
  gives the palette.
- a **role** for the candidate list, using the same kinds `smeltery run` logs: `prep`, `search`, `pair`, `field`, `gate`,
  `recorded`, `measure`, `rank`. Only `rank` (the paired cut) removes candidates. A gate either passes everyone or
  stops the run.
- the **artefacts** it needs and provides: `receptor`, `field`, `parent_poses`, `poses`, `dE_int`, `survivors`. These are
  the edges a frontend draws between stages.
- typed **knobs** (`Param`: `int`, `float`, `bool`, `str`, `path`, `choice`, `vec3`, `int_list`, `str_list`, `sites`),
  so forms can be generated. Knob names are the config's own keys. The knobs for `PairedPoses` and `FieldInteraction`
  are read from the tier dataclasses at their own defaults.
- for a tier, what the tier declares about itself: produced quantities and units, systematic floors and
  `is_delta_g`.

Every registered tier is in the catalog. If `smeltery run` has no stage for a tier (`surface_esp`, `qmmm`,
`rescoring`), it appears with `wired=False` and the reason. The palette shows it but `addable()` does not offer it,
and `check()` refuses it.

## Editing and checking

```python
from smeltery.gui import Pipeline

p = Pipeline.minimal("demo", "benzoic", {"benzoic": "OC(=O)c1ccccc1", "4-F": "OC(=O)c1ccc(F)cc1"})
p.set("pocket", site=[{"q": 1.0, "atom": 0, "from_atom": 1, "distance": 3.0}])
p.set("cut", keep=1, floor=0.0)
p.add("forcefield")  # placed where smeltery run executes it; add(key, at=i) for a drop position
for issue in p.check():  # every issue at once: error / warning / note, each naming its stage
    print(issue)
print(p.to_toml())  # the config `smeltery run` reads; refuses (PipelineError) while errors remain
```

`check()` never raises. In order, it looks at:

1. Structure: unknown, duplicate, unwired or mutually exclusive methods, and required ones that are missing.
2. Dataflow: a stage placed before the stage that provides what it needs is an error. A reordering that moves no
   data, such as two gates swapped, gets a note giving the order `smeltery run` will actually use.
3. Knob types.
4. Advice: PoseBusters without `relax_unmapped` (a warning), and strain recorded with nothing rejected (a note). A cut
   without an explicit floor while the tier's floor is unmeasured is an error.
5. `smeltery.cli.validate_config` on the compiled config. The GUI keeps no second copy of the config rules, so a
   pipeline that checks clean is one the CLI accepts.

`Pipeline.from_config` / `from_toml` read a config back. Every file in `examples/*.toml` round-trips unchanged
(`tests/test_gui_pipeline.py`). `view()` returns one row per stage in execution order, including the implicit
parity gate, with the candidate count in and out for drawing the funnel.

## Not yet

- No frontend.
- No cost per stage: tier `estimate_cost` needs posed candidates. Running `--plan` from the GUI would give real ones.
- One ranked stage only, as in `smeltery run`. A second ranker would need a measured floor, and none of the wired
  ones has one.
