# What this changes

<!-- One or two sentences. Describe what is different afterwards. -->

## Review lines

Answer the lines that apply and mark the rest N/A.

- [ ] **Is this a workflow, or cron pretending to be one?** Periodic
      single-step work uses cron. The durable engine is for sequences that must
      resume after a crash halfway through.
- [ ] **Does a shape change update its public schema and contract surface?**
      Persisted shapes, wire shapes, error codes, CLI flags, and seam signatures
      change their public schema/docs in this PR. Maintainers also synchronize
      the private decision pack; external contributors should describe the
      impact so a maintainer can make that companion change.
- [ ] **Does a new number have one public home?** Every core tunable lives in
      `adopt_const`; application code and prose reference it rather than copying
      the literal. Maintainers own private cross-repository synchronization.
- [ ] **Does every new file belong in Apache-2.0 `adopt-core`?** Do not submit
      control-plane code, client material, credentials, or anything that cannot
      be redistributed permanently under this repository's licence.
- [ ] **Does a new dependency have a licence row?** All seven verification
      fields are required, or the gate blocks it. A missing row fails closed as
      an in-binary dependency.
- [ ] **Can each new test complete the defect sentence?** “Fails when ___
      breaks; matters because ___; no other instrument catches it because ___.”
      Test count and line coverage are not targets.
- [ ] **Has a new gate been watched failing?** Plant the violation, watch the
      gate reject it, then revert the plant byte-for-byte.

## Validation

<!-- Paste commands actually run and their output. -->

```text
```

## Contracts and documentation touched

<!-- Name public schema/docs changed, or explain why none were required.
     Maintainers: link the synchronized private-pack change when applicable. -->
