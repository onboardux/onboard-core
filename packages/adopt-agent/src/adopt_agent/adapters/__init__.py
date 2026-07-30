"""The only package permitted to import a model-provider client SDK.

Empty by design at S0. The four adapters (`anthropic`, `openai`, `local_openai`,
`fake_recorded`) land in S7.

This package exists now so the `no-provider-sdk` import contract has a concrete
target from the first commit. Adapters translate; they never enforce policy.
"""
