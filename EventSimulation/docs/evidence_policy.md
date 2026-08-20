# Evidence Policy

This policy is a hard requirement for every Gate.

## Core rule

No actor, identity, stance, utterance, fact, or relationship may be invented.
Every accepted item must trace to a real `case1` document through:

```text
item_id -> graph episode UUID -> source_id -> document_id -> accepted document
```

## Gate 2.1

- Brief executive summaries and topic summaries are synthesis, not atomic facts.
- Facts come from `prepared_analysis.reported_facts` or evidence-backed
  timeline/key-metric records.
- Claims come from explicit named/supporting/social views and interpretations.
- Every retained fact, claim, and timeline item must pass accepted-document
  text coverage and number checks.
- Failed or undated candidates are quarantined.
- Social material cannot become a fact.

## Gate 3

- Only strict `seed_version: 1.1` may enter the graph.
- Only the `<case_id>:real:<version>` group is written.
- Each real fact or claim is one structured JSON Episode.
- Zep receives structured JSON Episodes and is not allowed to become the source
  of truth for the Seed; it must not use world knowledge or infer
  unstated content.
- A node is accepted only when its name is explicit in the Episode text.
- An edge is accepted only when it references the producing Episode, both
  endpoint nodes are grounded, and its fact is supported by Episode text.
- Unsupported or untraceable graph outputs are not returned by accepted
  query results. Unsupported new relationships are physically deleted, and the
  queries. The local manifest remains authoritative for source traceability.

## Gate 4

- Agent count is determined by evidence, not by a configured target.
- A Persona requires a real Seed entity and an explicit claim where that entity
  is the speaker or organization.
- Persona utterance units are copied from referenced facts and claims.
- No generated biography, demographic data, stance, relationship, or wording.
- Every Persona remains `pending_human_review`.

## Gate 5+

Gate 5 must not start until the Seed, graph manifest, and Persona candidates
are manually approved. Any future utterance must carry fact/claim reference IDs
and pass the same grounding policy before it can enter simulation memory.
