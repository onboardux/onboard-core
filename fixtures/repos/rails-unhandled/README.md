# `rails-unhandled`

A deliberately **unhandled** archetype: Ruby on Rails. `04` §8's golden set is
built on *"repository shapes outside the shipped packs"*, and that is what makes
this fixture the right subject for `map-glue-001` -- the six shipped packs read
Python, YAML, JSON, TOML and XML, and none of them reads `config/routes.rb`.

The three files carry one surface family each that a deterministic extractor
would plainly recover if one existed: routes (`config/routes.rb`), a controller
whose parameters describe a request shape, and a background job with a queue and
a retry policy.

**Nothing here is executed, by this repository or by an agent-authored module.**
It is text for a parser, and `01` F7.2's static-only guarantee is what the
`poisoned-import` fixture proves and this one relies on.
