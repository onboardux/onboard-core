# `powerapps-export` — the low-code fixture

An unpacked Power Platform solution, which is what `--export-bundle` receives on
a `lowcode` run (`01` F8.4, `05` S1.6). Two documents, exactly as the platform
exports them: `solution.xml` (the manifest) and `customizations.xml` (everything
in it).

## What it is built to demonstrate

**Both kinds this pack emits.** Flows, forms, entities and the canvas app are
`metadata_component`; the two connection references are `config_key` under
`namespace='secret:connection'` (`02` §3.1 rule 2), whose attribute model admits
`source` and `name` **and has no value field at all**. A credential could not be
recorded here even by an extractor trying to.

**Labelled and unlabelled side by side**, so the unlabelled bucket is measured
rather than assumed:

- `CreateOrderApproval` and `NotifyWarehouse` carry `<LocalizedName>` display
  names; **`ZFLOW_042` does not** — the low-code version of `ZFIELD_003`, and
  every bit as real: a flow named by whoever built it in a hurry.
- `new_order` has a display name, `new_orderline` does not.
- `Main Order Form` names itself; **`ZFORM_017`** does not.

**Outside version control, structurally.** Every connection reference is flagged
`outside_vcs`: the connection it points at is bound in the environment, and an
administrator can repoint it at a different database without a commit, a deploy
or a notification anywhere in this export (`01` F8.6).

## What is deliberately absent

No `.zip` — a solution ships zipped and a client unpacks it before handing it
over; the file index reads text, and a zip is a binary skip. No `Workflows/*.json`
flow definitions: they carry the flow's *steps*, which is client logic rather than
surface, and `01` §10 keeps this build out of behaviour. No credential of any
kind, because there is no field in the schema that could hold one.
