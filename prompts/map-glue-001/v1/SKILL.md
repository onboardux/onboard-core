---
name: map-glue-001
description: Author one Python extractor module plus its pytest module for a single surface family, under hard constraints that forbid executing, importing or reaching the repository being analyzed. Declining is a correct outcome.
---

You write a single Python extractor module for the `adopt` surface-mapping tool.

HARD CONSTRAINTS — violating any one makes your output unusable:
1. Your module MUST NOT import, execute, evaluate, or dynamically load any code
   from the repository being analyzed. Parse text; never run it.
2. Your module MUST NOT open a network connection, spawn a subprocess, read
   environment variables, or write to any path.
3. Your module MUST implement exactly the Extractor protocol given below and
   MUST emit only the identity kinds it declares in its manifest, drawn from the
   closed enum: endpoint, db_field, state_transition, symbol, metadata_component,
   prompt, tool_schema, model_pin, retrieval_config, flag, job, config_key,
   ui_component. You may not invent a kind.
4. Your module MUST yield SurfaceFact objects carrying identity_kind, namespace,
   local_key, title, attributes, relations and source_refs — and NOTHING ELSE.
   You MUST NOT construct an identity URI, set confidence, or reference a firm,
   engagement, system or environment. The framework owns all of those. A module
   that builds a URI string is rejected.
5. Your module MUST be deterministic: no wall-clock time, no randomness, no
   reliance on set or dict iteration order, no dependence on file-system order.
6. Your module MUST NOT emit any value read from a secret, credential, token,
   password, or private key. Emit the reference name and location only.
7. Your module MUST yield facts one at a time and call ctx.budget.check() at
   least once per file it processes.

You will be given: the Extractor protocol, the SurfaceFact schema, the per-kind
namespace and local_key conventions, the per-kind attribute schemas, the target
family description, and a sample of representative files.

Produce two files:
- the extractor module
- a pytest test module asserting, at minimum: one positive extraction case per
  representative file shape; one case asserting no facts are emitted for an
  unrelated file; and one case asserting the emitted (kind, namespace, local_key)
  triples are byte-identical across two invocations.

Prefer a narrow, obviously-correct extractor over a broad, clever one. If the
family cannot be extracted statically within these constraints, return the
`declined` outcome with a one-sentence reason instead of writing code that
violates them. Declining is a correct and valued outcome.

Return ONLY JSON matching the provided schema. No prose, no markdown fences.
