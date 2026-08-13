"""`adopt map` -- Build 1's system surface map.

Sprint S1.1 lands scope resolution, URI minting and the revision-aware writer.
Every shape this build adds lives here rather than in a Build 0 package
(B1-CR-28): no Build 0 code reads a `SurfaceFact`, and keeping them here means
the ownership rule in `03-implementation-spec.md` §4 never has to be bent.
"""
