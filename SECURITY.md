# Security policy — `adopt-core`

## Reporting a vulnerability

Report privately through GitHub's "Report a vulnerability" flow on this
repository. Do not open a public issue for a suspected vulnerability.

Include: affected version (`adopt version --json`), the platform, a minimal
reproduction, and the impact you believe it has. **Do not include client source
code, knowledge item bodies, prompts, model output, or secrets** in a report —
see the disclosure posture below.

You will get an acknowledgement within 3 working days and a remediation plan or
a rejection with reasoning within 10 working days.

## Security posture this project commits to

These are enforced by CI gates, not by convention. A change that breaks one is a
revert, not a review comment.

| Property | Mechanism |
|---|---|
| **No telemetry in OSS mode, permanently** | There is no opt-in switch and none will be added. The control plane is the telemetry path. |
| **Offline is the default posture** | In offline mode the process opens no socket other than to a configured local adapter endpoint. Enforced by a blocked-socket property test. |
| **No client content in logs or telemetry** | Deny-listed fields (`body`, `content`, `prompt`, `output`, `source`, `text`, `answer`, `question`) are dropped at the sink and counted. Enforced by a planted-secret property test. |
| **Secrets are never persisted** | Read once at process start into a typed config object. Never written to a store, a trace, an error message or a log line. `adopt doctor` prints presence and source, never value. |
| **No model-authored code is executed** | Agent-authored source is written to a review directory and never executed in the run that produced it. |
| **Supply chain** | Every release ships a CycloneDX SBOM, SLSA provenance and cosign signatures. A release missing any of the three is not a release. |
| **Dependency licences** | Permissive-only in-binary; copyleft subprocess-only. Enforced by `scripts/licence_gate.py` on every PR and weekly on a schedule. |

## Verifying a release

```sh
cosign verify-blob --signature adopt-<version>-<platform>.sig adopt-<version>-<platform>
adopt version --json    # reports version, schema_version, export_version, sbom_sha256, build_id
```

## Supported versions

Pre-1.0, only the latest minor release receives security fixes.

A store written by a newer binary opens **read-only** under an older binary and
is never upgraded, downgraded or repaired in place. That is a safety property,
not a limitation: it is what makes rolling a binary back a safe operation.
