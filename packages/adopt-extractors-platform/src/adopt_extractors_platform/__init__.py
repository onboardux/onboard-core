"""`adopt-extractors-platform` -- the packaged-platform pack (`03` §5.10, `05` S1.6).

Three readers over one archetype: a Salesforce metadata retrieve, an SAP
transport object list, a ServiceNow update set. All three normalise into
`metadata_component`, which is `adopt-extractors`' third doctrine -- *"an SAP
metadata component and a Django route land in the same shape"* -- and the reason
this pack adds no kind and no attribute model.

**What makes this pack different is not the parsing, it is the honesty.** The
web pack reads code and the AI pack reads settings; this one reads a vendor's
export of a system whose names were never written for an outsider. Design
Appendix B states the limit plainly: *"the metadata is retrievable but
meaningless alone -- a field called `ZFIELD_003` tells you nothing -- so day-one
competence is genuinely worse until a human does a labelling pass. The design
says so out loud, because a confidently wrong answer in week one costs the
account."* So the pack's job is to enumerate completely, label **only** where
the bundle labels, and hand everything else to `adopt_map.unlabeled` for a human.

**No platform SDK, no connection, no XML dependency.** The bundle is the subject
(`01` F8.3, `01` §10), and every document goes through `adopt_map.xmlsafe`.
"""

from adopt_map.schemas import Extractor

from adopt_extractors_platform.sap_transport import SapTransportExtractor
from adopt_extractors_platform.sf_metadata import SfMetadataExtractor
from adopt_extractors_platform.snow_updateset import SnowUpdateSetExtractor

__all__ = [
    "SapTransportExtractor",
    "SfMetadataExtractor",
    "SnowUpdateSetExtractor",
    "pack",
]


def pack() -> tuple[Extractor, ...]:
    """Every `platform` extractor a real run may use, in manifest-id order."""
    return (
        SapTransportExtractor(),
        SfMetadataExtractor(),
        SnowUpdateSetExtractor(),
    )
