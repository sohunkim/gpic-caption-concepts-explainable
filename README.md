# GPIC Caption Concepts Explainable

Explainable, inventory-backed caption-to-concept extraction for GPIC captions.
The pipeline favors documented and inspectable rules over hidden recovery
heuristics. It supports sentence and tag-list captions, OEWN-backed object,
attribute, and action inventories, preposition MWEs, canonicalization, and
count export.

## Pipeline

| Stage | Purpose | Main output |
|---|---|---|
| 1 | Route sentence vs. tag-list captions | normalized caption rows |
| 2 | Protect quote/hyphen spans without object-MWE retokenization | tokenizable text |
| 3 | spaCy annotation | token, POS, dependency, noun-chunk records |
| 3.5 | Build/resolve object, attribute, and action inventories and Stage 5 lexicons | versioned inventory bundle |
| 4 | Extract raw mentions and edges | raw mentions/edges |
| 5 | Apply inventory canonical labels and parent evidence | canonical mentions/edges |
| 6 | Export facts and count tables | TSV/JSONL count artifacts |

The complete behavioral contract is [docs/rules_v1.md](docs/rules_v1.md).
Known omissions are listed in
[docs/known_limitations_v1.md](docs/known_limitations_v1.md).

## Environment

Windows setup:

```powershell
.\scripts\setup_env.ps1
```

The checked environment is described by `environment.yml` and uses Python
3.11, spaCy 3.8 with `en_core_web_trf`, NLTK, and `wn==1.1.0`.

MLXP/Linux runtime setup:

```bash
bash scripts/setup_mlxp_runtime.sh
```

Formal MLXP runs must use a clean, verified Git checkout or an immutable code
snapshot as described in `AGENTS.md`.

## Official Entrypoints

- `scripts/run_mixed_caption_pipeline.py`: sentence/tag-list Stage 1-6 run.
- `scripts/run_stage35_inventory_workflow.py`: ordered inventory resolution and
  canonical lexicon workflow.
- `scripts/run_stage456_sharded.py`: sharded fixed-inventory Stage 4-6 run.
- `scripts/publish_inventory_bundle.py`: publish a complete synchronized
  inventory and Stage 5 lexicon bundle.
- `scripts/publish_current_inventory_component.py`: object-only component
  publication. Attribute/action updates require a complete bundle because they
  must remain synchronized with Stage 5 lexicons.
- `scripts/refresh_current_inventory_metadata.py`: recompute current bundle row
  counts and repair local provenance pointers without replacing inventory data.
- `scripts/build_interactive_count_report.py`: build an interactive count report.
- `scripts/build_quote_free_interactive_report.py`: derive a conservative
  quote-noise-filtered report copy.

Every formal runner is subject to the repository incident gate. An unresolved
`.pipeline_state/incident.json` blocks another official run until its root cause,
durable guard, and verification evidence are recorded.

## Current Inventory

The active input is the single bundle manifest:

```text
resources/gpic_inventory/current/inventory_bundle.json
```

Use that manifest instead of manually mixing inventory paths from historical
output directories. It binds:

- object inventory
- attribute inventory
- action inventory and action canonical inventory
- matching Stage 5 attribute/action synonym lexicons

`object_synonyms.tsv` and `object_parents.tsv` may contain only headers. This is
intentional: object canonical and immediate-hypernym evidence travel in the
Stage 4 object inventory source detail. Attribute and action canonicalization
uses the Stage 5 synonym TSVs.

The reviewed preposition-MWE lexicon is a versioned repository resource at
`resources/lexicons/preposition_mwes.tsv`; therefore a reproducible run records
both the inventory bundle and Git commit.

## Tests

```powershell
.\scripts\run_tests.ps1 --pytest -q --timeout-seconds 900
```

`pytest.ini` restricts collection to `tests/`, so generated or externally
copied code under `outputs/` cannot become part of the repository test suite.

Before a long run, validate the active workspace and current bundle metadata:

```powershell
.\scripts\assert_active_workspace.ps1
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py `
  --timeout-seconds 120 -- scripts\refresh_current_inventory_metadata.py
```

The refresh command changes metadata only; it does not replace inventory rows.

## Reports

Interactive reports contain SQLite-backed pagination and filters rather than
embedding every row in one HTML document. Build quote-free copies separately so
the formal extraction/count artifacts remain unchanged. On Blackwell, an
already extracted quote-free report can be started with:

```bash
REPORT_DIR=/path/to/report REPORT_PORT=8771 \
  bash scripts/start_blackwell_quote_free_report.sh
```

Validate quote-free report labels with:

```powershell
.\scripts\run_python.ps1 scripts\validate_interactive_report_db.py `
  --report-db <report-dir>\report.db `
  --forbid-leading-quoted-labels
```

Generated `outputs/` artifacts are not source-of-truth code and are not part of
normal Git handoff.
