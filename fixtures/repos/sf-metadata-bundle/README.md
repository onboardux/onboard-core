# `sf-metadata-bundle` — the packaged-platform fixture

What `--export-bundle` is handed on a `platform` run (`01` F8.3, `05` S1.6).
There is no source tree here on purpose: **a packaged platform has none, and the
metadata is the subject**.

## Why three vendors sit in one bundle

A real client hands over one vendor's export. This fixture carries three because
the `platform` pack has three readers and the conformance suite gives a pack
**one** tree (`tests/conformance/`, B1-CR-69) — and an obligation asserted over an
extractor that emitted nothing is an obligation asserted over nothing. Read the
three subdirectories as three separate client exports that happen to share a
directory for testing, not as a shape any platform produces.

| Path | Reader | Stands for |
|---|---|---|
| `salesforce/` | `platform.sf_metadata` | `sfdx force:source:retrieve` output |
| `sap/` | `platform.sap_transport` | an SE01/SE10 transport object list |
| `servicenow/` | `platform.snow_updateset` | a `sys_remote_update_set` export |

## What it is built to demonstrate

**The honest limit, with both halves present.** Design Appendix B: *"a field
called `ZFIELD_003` tells you nothing."* This bundle has labelled components and
unlabelled ones, so the unlabelled bucket has a denominator and the first
screen's ratio is a real measurement rather than 100% by construction:

- `Order__c` labels `Status__c` and `Account__c`, and does **not** label
  `ZFIELD_003__c` or `ZFIELD_007__c` — the canonical case, verbatim from the
  design document.
- `Account` labels `Region__c` and not `Legacy_Code__c`.
- **Every** SAP and ServiceNow component is unlabelled, which is not a gap in the
  fixture: an object list and an update set genuinely carry no human label, and a
  fixture that invented one would be testing a claim the format cannot support.

**A payload that must never be read.** Each `sys_update_xml` carries a
`<payload>` holding the customised record — a Script Include's source, a business
rule's condition. `03` §5.9 invariant 4 forbids client source content in any
artefact, and `platform.snow_updateset` steps over the element rather than
recording it. `tests/integration/test_platform_bundle.py` asserts none of it
reaches `surface.json`.

## What is deliberately absent

No `.zip`, no co-file, no binary transport cluster: the file index skips binaries
and nothing honest could be read from them without SAP's own tooling. No live
credentials and no connection string — `01` §10 rules a live platform connection
out of this build entirely, so there is nothing here to connect with.
