---
name: map-prose-001
description: Write one paragraph of at most 60 words describing a single surface element from its structured attributes alone, for an engineer who has never seen the codebase. The empty string is correct when the attributes are too thin.
---

Write one paragraph, at most 60 words, describing a single element of a software
system for an engineer who has never seen this codebase.

You will be given the element's structured attributes only. Describe what those
attributes state. Do not speculate about purpose, quality, business meaning, or
history. If the attributes are too thin to describe, return the empty string —
an empty description is better than an invented one.

Do not use the words "robust", "seamless", "powerful", "simply", or "leverages".
Return ONLY JSON matching the provided schema.
