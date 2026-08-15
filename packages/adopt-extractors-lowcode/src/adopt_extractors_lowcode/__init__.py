"""`adopt-extractors-lowcode` -- the low-code pack (`03` §5.10, `05` S1.6).

One reader over one archetype: an unpacked Power Platform solution. `01` F8.4
files its flows, forms and apps as `metadata_component` and its connection
references as `config_key` under a `secret:*` namespace, so the pack adds no kind
of its own -- the fourth archetype normalising into the same thirteen-kind
vocabulary, which is what `03` §5.10's table exists to demonstrate.

**One extractor is not a small pack, it is an honest one.** A solution export is
a single document family; splitting it into three readers over the same two files
would triple the parse cost and give three extractors one referent apiece to
disagree about -- B1-CR-68's situation manufactured deliberately.

**No SDK, no connection, no XML dependency**: `adopt_map.xmlsafe` reads the
bundle, and the credential a connection reference points at lives in the platform
where we can neither see it nor hold it (`02` §5.1 rule 4).
"""

from adopt_map.schemas import Extractor

from adopt_extractors_lowcode.solution_package import SolutionPackageExtractor

__all__ = ["SolutionPackageExtractor", "pack"]


def pack() -> tuple[Extractor, ...]:
    """Every `lowcode` extractor a real run may use, in manifest-id order."""
    return (SolutionPackageExtractor(),)
