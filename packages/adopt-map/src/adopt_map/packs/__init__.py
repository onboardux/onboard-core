"""Archetype packs, as **modules** of one distribution (v6.1 §6).

Not seven wheels. The v4-line build shipped a distribution per pack and the CLI
imported six while declaring one, so `pip install adopt-cli` produced a
`ModuleNotFoundError` on the flagship command for every project and every
archetype (B-10). Modules make that unrepresentable: if `adopt_map` imports, its
packs import.

`registry()` is a function rather than a module-level dict because construction
is cheap and a shared mutable registry is how one test's registration leaks into
another's expectations.
"""

from adopt_map.packs import generic, web
from adopt_map.runner import Pack

__all__ = ["registry"]


def registry() -> dict[str, Pack]:
    """Every pack this release ships, by name.

    Registration and enablement stay two decisions: everything registered here
    is *available*, and `select_packs` decides what actually runs. That is what
    lets `MAP_NO_PACK_FOR_ARCHETYPE` distinguish "no pack for this archetype"
    from "that pack does not exist" -- two different sentences needing two
    different fixes.
    """
    return {
        "generic": Pack(
            name="generic",
            extractors=(
                generic.DependencyExtractor(),
                generic.ConfigKeyExtractor(),
                generic.EnvVarExtractor(),
                generic.SettingsClassExtractor(),
                generic.ScheduledJobExtractor(),
                generic.CiWorkflowExtractor(),
                generic.FilesOfInterestExtractor(),
            ),
        ),
        "web": Pack(
            name="web",
            extractors=(
                web.EndpointExtractor(),
                web.MiddlewareExtractor(),
                web.SchemaFieldExtractor(),
            ),
        ),
    }
