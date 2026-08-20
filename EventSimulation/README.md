# EventSimulation

Traceable case-to-simulation workflow for turning an existing `My_agent` case
into a time-bounded Seed, a real-evidence Zep Cloud graph, evidence-derived
Personas, and a single-platform OASIS simulation.

The non-invention rules are normative and documented in
[`docs/evidence_policy.md`](docs/evidence_policy.md).

Copy `.env.example` to `.env` and fill in the database, Zep Cloud, and
OpenAI-compatible model settings before using the integration commands.

The project does not fetch pages or write to PostgreSQL. The case loader only invokes
the three existing repository read methods:

- `get_case(case_ref)`
- `aggregate_case_prepared_analysis(case_ref)`
- `list_case_candidates(case_ref)`

The strict seed builder uses `brief_data` only for dated timeline/key-metric
structure and uses `prepared_analysis.reported_facts` for atomic facts. Brief
summaries are synthesis and are not promoted to facts. Media reports that
explicitly attribute a public statement to a regulator, central bank, or
exchange may be classified as `official_fact`; the actual media document
remains the evidence source. A retained fact/claim must also pass normalized
text coverage and numeric-token checks against the accepted document body;
failed candidates go to `quality.content_grounding_quarantine` without copying
the body into the Seed.

## Code structure

The source is organized by engineering responsibility rather than Gate number:

```text
event_simulation/
  models/ repositories/ services/ integrations/ runtime/ workers/
```

Gate 1-7 remain workflow acceptance stages in the documentation, not Python
package boundaries.

## Case and Seed

After code review, install `My_agent` and this package in the same environment,
then run:

```bash
event-simulation build \
  --case case1 \
  --as-of 2026-06-17T23:59:59+08:00 \
  --horizon-hours 48 \
  --output-dir artifacts/case1
```

By default every real fact, claim, and timeline item must have a dated source
at or before `--as-of`. For the current case1 exploratory run only, the
temporal gate for undated Weibo/social claims can be disabled explicitly:

```bash
event-simulation build \
  --case case1 \
  --as-of 2026-06-17T23:59:59+08:00 \
  --horizon-hours 48 \
  --allow-undated-social \
  --output-dir artifacts/case1
```

This does not admit undated facts, timelines, media sources, or unsupported
content; it only admits claims whose trace is explicitly social/Weibo.

This writes `case_bundle_audit.json`, `case_bundle_audit.md`, and `seed.json`.

## Real Graph (Zep Cloud)

Install the optional graph dependency and configure Zep Cloud:

```bash
pip install -e '.[graph]'

event-simulation graph-init \
  --seed artifacts/case1/seed.json \
  --manifest artifacts/case1/graph_manifest_v3.json \
  --real-version v3

event-simulation graph-query \
  --manifest artifacts/case1/graph_manifest_v3.json \
  --output artifacts/case1/graph_acceptance.json
```

Graph initialization follows the MiroFish actor-ontology pipeline. It generates
and strictly validates ten speakable actor types plus six to ten constrained
relationship types from the accepted Seed and simulation question. The
ontology is saved beside the manifest and registered with Zep before any text
is uploaded. Accepted facts and claims are cleaned, split with the 500/50
character policy, and tracked individually until every Zep episode reports
that processing is complete. Graph initialization fails closed on an invalid
ontology, incomplete upload, processing timeout, or empty extracted graph.

The graph manifest preserves the original trace and source IDs. A separate
Chinese localization artifact stores accurate display translations for node
names, summaries, relationship names, and relationship facts; internal Zep
identifiers remain unchanged.

Gate 3 rejects older `seed_version: 1.0` artifacts. Regenerate a strict
`seed_version: 1.1` after reviewing the code before initializing the graph.

## Personas

```bash
event-simulation personas \
  --seed artifacts/case1/seed.json \
  --manifest artifacts/case1/graph_manifest_v3.json \
  --output-dir artifacts/case1
```

Review `personas.md` and approve only the real entities you want to simulate:

```bash
event-simulation personas-approve \
  --input artifacts/case1/personas.json \
  --output artifacts/case1/personas_approved.json \
  --persona-id persona_xxx
```

If `--persona-id` is omitted, every generated candidate is approved; use that
only after reviewing the complete candidate table. Approval confirms identity,
entity-to-claim mapping, and role classification. It does not ask you to write
new biographies or stances.

Persona count is evidence-determined, never fixed. A Persona is created only
from a Zep node carrying one of the registered custom actor labels; nodes with
only the default `Entity`/`Node` labels are excluded. The Persona receives the
actor node's translated name, ontology type, summary, attributes, incident
relationships, and traceable evidence references. No unsupported biography or
stance is invented. All Personas remain `pending_human_review`. Gate 5 must not
start before approval.

## OASIS simulation and results

Install the pinned OASIS integration when you are ready to run a simulation:

```bash
pip install -e '.[simulation]'
```

The simulation runtime is an AGPL-3.0 simplification of MiroFish's Twitter
orchestration. Case ingestion, evidence policy, approved Personas and result
provenance remain EventSimulation-specific.

Create, run four rounds, and produce the final report with one command:

```bash
event-simulation simulation run \
  --case case1 \
  --seed artifacts/case1/seed.json \
  --personas artifacts/case1/personas_approved.json \
  --artifacts-root artifacts \
  --rounds 4
```

For background execution, create a run first:

```bash
event-simulation simulation create \
  --case case1 \
  --seed artifacts/case1/seed.json \
  --personas artifacts/case1/personas_approved.json \
  --artifacts-root artifacts \
  --rounds 4
```

Start it in the foreground or as a background process:

```bash
event-simulation simulation start artifacts/cases/case1/runs/<simulation_id> --foreground
event-simulation simulation status artifacts/cases/case1/runs/<simulation_id>
```

Round 0 uses a grounded initial event. Rounds 1-4 use OASIS `LLMAction` so
simulation agents can form new simulated opinions and relationships. These
outputs are labeled `origin=simulation`, stored in the run's isolated memory,
and never promoted into the real graph.

Each run directory contains:

```text
definition.json                 immutable run definition
simulation_config.json          rounds, time and enabled behavior types
personas.json                   approved Persona snapshot
twitter_profiles.csv            OASIS input
twitter/actions.jsonl           append-only round and behavior stream
oasis.db                        complete OASIS database and raw trace
memory/simulation_memory.json   high-information simulated memories
simulation_run.json             normalized round-by-round result
results/simulation_report.json  structured result summary
results/simulation_report.md    event trajectory and netizen-emotion report
results/rounds_detail.md        every round, Agent name, action and utterance
run_state.json                  lifecycle and progress
```

Gate 7 can analyze a completed run with:

```bash
event-simulation analyze-run \
  --seed artifacts/case1/seed.json \
  --run artifacts/cases/case1/runs/<simulation_id>/simulation_run.json \
  --holdout artifacts/case1/holdout.json \
  --output-dir artifacts/case1
```

The result document includes the simulated event trajectory, role views,
consensus and disagreement, propagation paths, and (when evidence-derived
netizen Agents exist) simulated netizen sentiment. It is labeled
`Simulation Result`, not a real-world fact, prediction, or investment conclusion.
