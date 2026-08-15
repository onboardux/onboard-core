# `dbt-warehouse` — the data & analytics fixture

A dbt project as a client's repository actually holds it: YAML and SQL text. The
`data` archetype takes **no** `--export-bundle` — unlike `platform` and
`lowcode`, a warehouse project *is* a source tree.

## What it is built to demonstrate

**Lineage with a direction that can be got wrong.** The graph here is deep enough
that reversing `derives_from` would still produce edges, which is what makes the
direction testable rather than merely present:

```
source.raw.orders     → stg_orders    ┐
source.raw.customers  → stg_customers ┘→ orders_daily → customer_revenue
                                          ↑
                                   semantic_model.orders → metric.revenue
                                                         → metric.orders_per_day
```

Every arrow above is read from the client's own `ref()` / `source()` / `model:`
declaration. `02` §5.2: *"the framework does not auto-create inverses — an
extractor emits the direction it observed"*, and what a `ref()` observes is **"I
depend on that"**.

**Documented and undocumented models side by side.** `schema.yml` describes
`stg_orders` and `orders_daily`; it says nothing about `stg_customers` or
`customer_revenue`, which is the ordinary state of a real warehouse. The two
undocumented models are unlabelled and reach the labelling queue — the same
mechanism as `ZFIELD_003`, arriving from a completely different direction. So
does `metric.orders_per_day`, which has no `label:` while `revenue` does.

**Document ownership, twice.** `common.config` reads every YAML file in a tree,
so without `adopt_map.documents` this project would be minted twice: once as
`metadata_component/dbt/model.orders_daily` and once as
`config_key/yaml/models.1.name` (B1-CR-74). `schema.yml` is claimed by `data.dbt`
through `models:` **plus** `version:`, `dbt_project.yml` through `profile:` plus
`name:`, and `semantic_models.yml` by `data.semantic_model`. The co-key matters:
`models:` alone is one of the most common top-level keys in YAML anywhere, and
claiming it on its own would make `common.config` silently skip an ordinary
settings file in some other repository.

## What is deliberately absent

No `target/manifest.json`. It is dbt's *compiled* artefact and producing it means
running dbt against a warehouse profile — a live connection `02` §7 obligation 1
forbids and `01` §10 rules out. The source is what a client's repository holds and
what this pack reads.
