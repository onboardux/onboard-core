"""Keeping the coverage cache current after a command that itself changed coverage.

**The alarm was firing on the product's own writes.** `identity.covered_cache`
is rebuilt *from* `recompute_coverage()` and never the reverse, and any
disagreement between the two alarms (`COVERAGE_CACHE_DISAGREEMENT`) rather than
self-healing. But only `adopt coverage recompute --rebuild` ever wrote the
cache, so every `ingest`, `bind`, `answer`, review resolution and `refresh`
left it disagreeing, and the next `adopt gaps` paged about a change the product
had just made on purpose. An alarm that fires on every ordinary write is one an
operator learns to ignore, which is exactly when the real one -- a cache some
defect wrote -- would go unread.

**The rule that keeps the alarm meaningful: refresh only a cache that agreed.**
The command recomputes first. If the cache already disagreed, something other
than this command wrote it, and `store.py`'s incident rule applies -- rebuilding
a disagreeing cache destroys the evidence of whatever wrote it -- so the cache is
left alone and the alarm keeps firing. Only when it agreed beforehand does the
command rebuild it after its own writes, from a recompute that does not compare,
because the comparison would only report the change this command intended.

The rebuild goes through `adopt_coverage.rebuild_cache`, so `no-covered-cache-write`
still holds: the CLI decides *when*, and `adopt_coverage` remains the only writer.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from adopt_coverage import CacheWriter, CoverageRecords

__all__ = ["CoverageStore", "coverage_cache_kept_current"]


class CoverageStore(Protocol):
    """The two things this module needs from a store handle, structurally.

    Declared rather than importing the SQLite handle, so `no-raw-sqlite` holds:
    `adopt_coverage`'s own ports are the whole surface.
    """

    @property
    def backend(self) -> CacheWriter: ...
    def coverage_records(self) -> CoverageRecords: ...


@contextmanager
def coverage_cache_kept_current(handle: CoverageStore, system_id: str | None) -> Iterator[None]:
    """Wrap a command body that changes coverage inputs for `system_id`.

    Args:
        handle: An open, writable store handle.
        system_id: The system whose coverage the body may change. `None` (a
            command that resolved no system) wraps nothing.

    The post-write rebuild runs only when the body returns normally: a body that
    raised has rolled its own transaction back, so there is nothing to follow.
    """
    if system_id is None:
        yield
        return

    from adopt_coverage import rebuild_cache, recompute_coverage

    before = recompute_coverage(handle.coverage_records(), system_id)
    yield
    if before.disagreements:
        return
    after = recompute_coverage(handle.coverage_records(), system_id, compare_cache=False)
    rebuild_cache(handle.backend, after)
