# What this changes

<!-- One or two sentences. What is different afterwards, not what you did. -->

## Review lines

These are the questions this codebase has learned to ask. Delete none of them;
answer the ones that apply and mark the rest N/A.

- [ ] **Is this a workflow, or is it cron pretending to be one?** Periodic
      single-step work uses cron (PRD F14.5). The durable engine is for
      sequences that must survive a crash *halfway*; a job that runs hourly
      survives by running again next hour, and putting it on the engine buys
      retention, replay and a queue nobody needed.
- [ ] **Does a shape change here also change `02`?** A persisted shape, wire
      shape, error code, CLI flag or seam signature changes in the contracts
      document **in this PR** (`00` §5 rule 1). No exceptions, including
      "temporary".
- [ ] **Does a new number have one home?** Every tunable lives in `adopt_const`
      or `plane_const` and in `03` §2, together, or `constants-sync` fails.
- [ ] **Which repository does each new file belong to?** Apache-2.0 cannot be
      un-published. Placement comes from `03` §1.2/§1.3 **before** the file is
      created; when the pack does not assign it, ask.
- [ ] **Does a new dependency have a licence row?** Seven fields, or the gate
      blocks it. A dependency with no row is treated as `in-binary` — failing
      closed.
- [ ] **Can each new test complete the defect sentence?** *"Fails when ___
      breaks; matters because ___; no other instrument catches it because ___."*
      Test count and line coverage are banned as targets.
- [ ] **Has any new gate been watched failing?** Plant the violation, watch the
      gate reject it, revert byte-exactly. A gate nobody has seen fail is a gate
      nobody should trust.

## Validation

<!-- Paste the commands you ran and their actual output. "CI will catch it" is
     not a validation record, and neither is a command you did not run. -->

```
```

## Documents touched

<!-- `00` register row, `02` §, `03` §, `05` checkbox — or "none, and none were
     required". Say which; silence reads as "not checked". -->
