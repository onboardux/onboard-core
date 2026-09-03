"""The two matcher tiers, and the document reader that feeds them.

Each test names the defect it would catch. The tier boundary itself -- that a
name match never becomes a binding -- is asserted end-to-end in
`test_binding_honesty`; these are the rules that make that outcome correct
rather than accidental.
"""

from pathlib import Path

import pytest
from adopt_knowledge import (
    IdentityView,
    body_digest,
    discover,
    match_document,
    name_matches,
    read_document,
    split_frontmatter,
    structural_matches,
)
from adopt_knowledge.documents import DEFAULT_AUDIENCE, DEFAULT_KIND

from adopt_obs import AdoptError, ErrorCode

SCOPE = "onboard-v1://acme/platform/api/prod"


def _view(key: str, kind: str = "endpoint", paths: tuple[str, ...] = ()) -> IdentityView:
    return IdentityView(identity_id=f"idn_{key}", uri=f"{SCOPE}/{kind}/-/{key}", source_paths=paths)


@pytest.mark.unit
class TestStructuralTier:
    def test_a_written_canonical_uri_binds(self) -> None:
        """*Fails when* the URI matcher stops resolving an exact address.
        *Matters because* it is the one form of reference that needs no
        inference at all -- the author addressed the referent. *No other
        instrument catches it because* the name tier would still produce a
        suggestion, so the document would look handled while silently demoting
        the strongest evidence the product accepts."""
        identity = _view("orders")
        body = f"The endpoint is {identity.uri} and it validates input."

        matched, ambiguous = structural_matches(body, [identity])

        assert [match.tier for match in matched] == ["uri"]
        assert matched[0].identity_id == identity.identity_id
        assert ambiguous == ()

    def test_a_path_resolving_to_exactly_one_identity_binds(self) -> None:
        """*Fails when* an unambiguous path reference stops binding. *Matters
        because* it is the common case in real documentation -- prose cites
        files, not URIs. *No other instrument catches it because* the ingest
        report counts bindings without saying which tier produced them."""
        identity = _view("refund", paths=("src/payments/refund.ts",))
        body = "Refund logic lives in `src/payments/refund.ts` and is idempotent."

        matched, ambiguous = structural_matches(body, [identity])

        assert [match.tier for match in matched] == ["path"]
        assert ambiguous == ()

    def test_a_path_naming_several_identities_binds_nothing_and_is_reported(self) -> None:
        """*Fails when* an ambiguous path picks a winner. *Matters because* "this
        file holds four endpoints" is not evidence about which one the prose
        describes, and guessing produces a false binding with full structural
        confidence -- the worst possible provenance for a wrong row. *No other
        instrument catches it because* the resulting binding is
        indistinguishable from a correct one in every column."""
        shared = ("src/api/routes.ts",)
        identities = [_view("orders", paths=shared), _view("refunds", paths=shared)]
        body = "Both handlers are defined in `src/api/routes.ts`."

        matched, ambiguous = structural_matches(body, identities)

        assert matched == ()
        assert ambiguous == ("src/api/routes.ts",)

    def test_a_path_may_be_cited_by_its_trailing_segments(self) -> None:
        """*Fails when* a document writing `payments/refund.ts` for a file the
        store recorded as `src/payments/refund.ts` stops matching. *Matters
        because* documentation routinely cites paths relative to a sub-tree.
        *No other instrument catches it because* the miss is silent -- the
        document simply binds to less than it describes."""
        identity = _view("refund", paths=("src/payments/refund.ts",))

        matched, _ = structural_matches("See `payments/refund.ts`.", [identity])

        assert len(matched) == 1

    def test_prose_containing_a_slash_is_not_a_path(self) -> None:
        """*Fails when* the bare-token rule loosens. *Matters because*
        `and/or`, `read/write` and `he/she` appear in ordinary writing, and a
        matcher treating them as paths would bind on grammar. *No other
        instrument catches it because* such a match only fires when a store
        happens to hold a colliding path."""
        identity = _view("orders", paths=("and/or",))

        matched, ambiguous = structural_matches("Use and/or as appropriate.", [identity])

        assert matched == ()
        assert ambiguous == ()


@pytest.mark.unit
class TestNameTier:
    def test_a_key_appearing_as_a_token_is_suggested(self) -> None:
        """*Fails when* the name tier stops proposing anything. *Matters
        because* the queue is the only route by which a name match can ever
        become a binding, so a silent name tier makes the review surface empty
        and the product's binding recall collapse to structural references
        alone. *No other instrument catches it because* an empty queue looks
        exactly like a clean one."""
        identity = _view("DATABASE_URL", kind="config_key")

        matched = name_matches("Set DATABASE_URL before boot.", [identity])

        assert [match.tier for match in matched] == ["name"]
        assert matched[0].evidence == "DATABASE_URL"

    def test_a_key_inside_a_longer_word_is_not_a_match(self) -> None:
        """*Fails when* the token boundary is dropped. *Matters because*
        `user` would then match `username`, `users` and `user_id`, filling the
        queue with noise that trains reviewers to confirm without reading. *No
        other instrument catches it because* the suggestion is plausible on its
        face."""
        identity = _view("user", kind="db_field")

        assert name_matches("The username is unique.", [identity]) == ()

    def test_an_already_bound_identity_is_not_suggested_again(self) -> None:
        """*Fails when* the exclusion is dropped. *Matters because* asking a
        human to confirm what is already bound is how a queue teaches people to
        click confirm without looking. *No other instrument catches it because*
        confirming a duplicate is harmless in the store and invisible in
        aggregate counts."""
        identity = _view("orders")

        assert name_matches("orders", [identity], exclude={identity.identity_id}) == ()

    def test_a_structural_match_is_never_also_a_suggestion(self) -> None:
        """*Fails when* the tiers stop being exclusive. *Matters because* the
        stronger evidence already bound it, and re-proposing it is the same
        confirm-fatigue failure one level up. *No other instrument catches it
        because* both rows would be individually correct."""
        identity = _view("refund", paths=("src/refund.ts",))
        body = "`src/refund.ts` holds refund."

        outcome = match_document(body, [identity])

        assert len(outcome.structural) == 1
        assert outcome.suggested == ()


@pytest.mark.unit
class TestDocumentReader:
    def test_frontmatter_beats_every_heuristic(self, tmp_path: Path) -> None:
        """*Fails when* declared metadata stops winning. *Matters because* the
        heuristics are guesses about someone else's document, and a guess that
        cannot be corrected is wrong permanently. *No other instrument catches
        it because* the guessed value is usually plausible."""
        path = tmp_path / "runbook.md"
        path.write_text(
            "---\ntitle: Refund Runbook\nkind: rationale\naudience: admin\n---\n\n# Ignored\n",
            encoding="utf-8",
        )

        document = read_document(path, root=tmp_path)

        assert document.title == "Refund Runbook"
        assert document.kind == "rationale"
        assert document.audiences == ("admin",)

    def test_the_path_supplies_an_audience_when_nothing_else_does(self, tmp_path: Path) -> None:
        """*Fails when* the path heuristic stops firing. *Matters because*
        `audience_tag` is one of the six coverage inputs -- an untagged item
        cannot carry coverage at all, so every ingested document would land as
        a permanent gap. *No other instrument catches it because* the item and
        its binding are written correctly."""
        directory = tmp_path / "docs" / "runbook"
        directory.mkdir(parents=True)
        path = directory / "restore.md"
        path.write_text("# Restore\n", encoding="utf-8")

        assert read_document(path, root=tmp_path).audiences == ("client_ops",)

    def test_an_untagged_document_falls_back_rather_than_landing_untagged(
        self, tmp_path: Path
    ) -> None:
        """The fallback is not cosmetic -- see the test above for why."""
        path = tmp_path / "notes.md"
        path.write_text("# Notes\n", encoding="utf-8")

        document = read_document(path, root=tmp_path)

        assert document.audiences == (DEFAULT_AUDIENCE,)
        assert document.kind == DEFAULT_KIND

    def test_the_digest_ignores_frontmatter_and_line_endings(self, tmp_path: Path) -> None:
        """*Fails when* the digest starts covering the whole file. *Matters
        because* a changed digest appends a knowledge revision, so a retagged
        audience or a CRLF checkout would rewrite history for every document in
        the repository. *No other instrument catches it because* the extra
        revisions are individually valid."""
        first = tmp_path / "a.md"
        first.write_text("---\naudience: admin\n---\n# Same\n\nBody.\n", encoding="utf-8")
        second = tmp_path / "b.md"
        second.write_bytes(b"---\naudience: end_user\n---\n# Same\r\n\r\nBody.\r\n")

        assert read_document(first, root=tmp_path).digest == (
            read_document(second, root=tmp_path).digest
        )

    def test_malformed_frontmatter_is_ingested_as_prose(self, tmp_path: Path) -> None:
        """*Fails when* a YAML error starts refusing the file. *Matters because*
        the knowledge in the document is lost over a typo in a field nobody
        required. *No other instrument catches it because* the refusal looks
        like correct strictness."""
        path = tmp_path / "broken.md"
        path.write_text("---\ntitle: [unclosed\n---\n\n# Real content\n", encoding="utf-8")

        document = read_document(path, root=tmp_path)

        assert "Real content" in document.body_md

    def test_a_missing_path_is_refused_rather_than_contributing_nothing(
        self, tmp_path: Path
    ) -> None:
        """*Fails when* an absent path is skipped. *Matters because* a typo and
        an empty directory would then look identical, and the operator would
        believe a corpus was ingested that never was -- the denominator failure
        this repository has been bitten by repeatedly. *No other instrument
        catches it because* the run exits zero with a plausible count."""
        with pytest.raises(AdoptError) as raised:
            discover([tmp_path / "nope"], root=tmp_path)

        assert raised.value.code is ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE

    def test_discovery_is_ordered_and_deduplicated(self, tmp_path: Path) -> None:
        """*Fails when* discovery order becomes filesystem order. *Matters
        because* item ids are minted in write order, so two runs over one tree
        would produce different ids and a different review queue. *No other
        instrument catches it because* both orders are internally consistent."""
        (tmp_path / "b.md").write_text("# B\n", encoding="utf-8")
        (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")

        found = discover([tmp_path, tmp_path / "a.md"], root=tmp_path)

        assert [document.path for document in found] == ["a.md", "b.md"]

    def test_frontmatter_splits_only_on_a_leading_fence(self) -> None:
        """A `---` horizontal rule mid-document is not frontmatter."""
        text = "# Title\n\nSome prose.\n\n---\n\nMore prose.\n"

        frontmatter, body = split_frontmatter(text)

        assert frontmatter == {}
        assert body == text

    def test_the_digest_is_stable_across_runs(self) -> None:
        """A pure function of the body, or idempotence is not a property."""
        assert body_digest("hello\n") == body_digest("hello\r\n")
