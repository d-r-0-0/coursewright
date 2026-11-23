"""Tests for the two-e-textbook detector.

Ported from COURSE_BUILDER ``tests/test_audit_text_contexts.py``.  Every
source test has a counterpart here.  Differences, each deliberate:

- launch URLs are synthetic (``gateway.publisher.example``, made-up ids);
- records are ``ExternalToolLaunch`` models, not dicts;
- ``FakeCanvas`` is replaced by the recorded transport in ``conftest.py``,
  so the real client code runs;
- the school-specific tool hosts are now a policy, tested separately.

They pin one behaviour: every launch into a single publisher product must
collapse to a single fingerprint, and the previous edition must not.
"""

from __future__ import annotations

import re

from synthetic_canvas import (
    COURSE_ID,
    LECTURE_TOOL_LAUNCH,
    PUBLISHER_NEW_ETEXT,
    PUBLISHER_NEW_NODE,
    PUBLISHER_OLD_NODE,
)

from coursewright.design.diagnostics import text_contexts as contexts
from coursewright.design.diagnostics.models import ExternalToolLaunch

NEW_BOOK_SIBLING_NODE_LAUNCH = PUBLISHER_NEW_NODE.replace("nodeId/11", "nodeId/12")
NEW_BOOK_SUPPORT_LAUNCH = (
    "https://gateway.publisher.example/rest/launchLTISupportTool/1000001/2000001_1000001_9001"
)
OLD_BOOK_ETEXT_LAUNCH = (
    "https://gateway.publisher.example/rest/launchBasicLTI/1000001/2000000_1000001_8001/3000098"
)


def make_item(
    title: str, launch_url: str, module_name: str = "Unit 01", source: str = "module item"
) -> ExternalToolLaunch:
    """Build the record shape the collectors produce."""
    return ExternalToolLaunch(
        source=source,
        module_name=module_name,
        item_id=1,
        title=title,
        launch_url=launch_url,
        context=contexts.build_context_fingerprint(launch_url),
        is_publisher_launch=contexts.is_publisher_launch(launch_url),
        is_legacy_lab_title=contexts.is_legacy_lab_title(title),
    )


class TestContextFingerprint:
    """One product must produce exactly one fingerprint."""

    def test_sibling_nodes_in_one_book_share_a_fingerprint(self) -> None:
        assert contexts.build_context_fingerprint(
            PUBLISHER_NEW_NODE
        ) == contexts.build_context_fingerprint(NEW_BOOK_SIBLING_NODE_LAUNCH)

    def test_etext_launch_matches_its_own_book(self) -> None:
        # The regression that mattered: the e-textbook item has no "nodeId"
        # marker, and the old positional fallback filed it as its own context.
        assert contexts.build_context_fingerprint(
            PUBLISHER_NEW_ETEXT
        ) == contexts.build_context_fingerprint(PUBLISHER_NEW_NODE)

    def test_support_tool_launch_matches_its_own_book(self) -> None:
        assert contexts.build_context_fingerprint(
            NEW_BOOK_SUPPORT_LAUNCH
        ) == contexts.build_context_fingerprint(PUBLISHER_NEW_NODE)

    def test_old_and_new_books_stay_separate(self) -> None:
        assert contexts.build_context_fingerprint(
            PUBLISHER_OLD_NODE
        ) != contexts.build_context_fingerprint(PUBLISHER_NEW_NODE)

    def test_old_book_etext_launch_does_not_masquerade_as_new(self) -> None:
        # The dangerous direction: the OLD book's product-level launcher must
        # not collapse into the NEW book's fingerprint via the shared account id.
        assert contexts.build_context_fingerprint(
            OLD_BOOK_ETEXT_LAUNCH
        ) != contexts.build_context_fingerprint(PUBLISHER_NEW_NODE)

    def test_fingerprint_keeps_the_product_id(self) -> None:
        fingerprint = contexts.build_context_fingerprint(PUBLISHER_NEW_NODE)
        assert fingerprint == "gateway.publisher.example/1000001/2000001_1000001_9001"

    def test_missing_launch_url_is_labeled_not_split(self) -> None:
        assert contexts.build_context_fingerprint("") == "(no launch url)"

    def test_unrecognised_host_keeps_path_up_to_a_per_item_marker(self) -> None:
        fingerprint = contexts.build_context_fingerprint(
            "https://labs.example.com/books/alpha/item/9"
        )
        assert fingerprint == "labs.example.com/books/alpha"


class TestPublisherClassification:
    """Instructor tools must not be counted as a second e-textbook."""

    def test_lecture_tool_is_not_a_publisher(self) -> None:
        assert not contexts.is_publisher_launch(LECTURE_TOOL_LAUNCH)

    def test_gateway_is_a_publisher(self) -> None:
        assert contexts.is_publisher_launch(PUBLISHER_NEW_NODE)

    def test_unknown_host_counts_as_a_publisher(self) -> None:
        # Fail-closed: an unrecognised host must surface, not hide.
        assert contexts.is_publisher_launch("https://labs.example.com/x")

    def test_empty_launch_is_not_a_publisher(self) -> None:
        assert not contexts.is_publisher_launch("")

    def test_split_separates_lectures_from_book_content(self) -> None:
        items = [
            make_item("U01R01: Chapter", PUBLISHER_NEW_NODE),
            make_item("U03V01: Lecture", LECTURE_TOOL_LAUNCH),
        ]
        publisher_items, tool_items = contexts.split_publisher_and_tool_items(items)
        assert [item.title for item in publisher_items] == ["U01R01: Chapter"]
        assert [item.title for item in tool_items] == ["U03V01: Lecture"]

    def test_policy_adds_an_institution_host_without_code_changes(self) -> None:
        policy = contexts.AuditPolicy(non_publisher_hosts={"tools.school.example"})
        assert not contexts.is_publisher_launch("https://tools.school.example/x", policy)
        assert contexts.is_publisher_launch("https://tools.school.example/x")

    def test_canvas_host_is_always_a_tool(self) -> None:
        policy = contexts.DEFAULT_AUDIT_POLICY.with_canvas_host("https://canvas.example.edu/")
        assert not contexts.is_publisher_launch("https://canvas.example.edu/courses/1", policy)


class TestLegacyLabTitles:
    """Only the retiring numbered family counts as legacy."""

    def test_numbered_lab_is_legacy(self) -> None:
        assert contexts.is_legacy_lab_title(
            "Live Virtual Machine Lab 8-2: Securing Computing Resources"
        )

    def test_shortened_names_are_legacy(self) -> None:
        assert contexts.is_legacy_lab_title("Live Lab 1-1 — Threat Vectors")
        assert contexts.is_legacy_lab_title("U01A06: Live VM Lab 1-1 — Threat Vectors")

    def test_course_prerequisite_item_is_legacy(self) -> None:
        assert contexts.is_legacy_lab_title("Pre-Requisite: Live Virtual Machine Labs")

    def test_new_book_setup_items_are_not_legacy(self) -> None:
        # False positives before the lab number was required.
        assert not contexts.is_legacy_lab_title("Live Virtual Machine Lab Preparation")
        assert not contexts.is_legacy_lab_title("Read: Live Virtual Machine Labs Quick Start")
        assert not contexts.is_legacy_lab_title("Watch: Getting Started with Live VM Labs")

    def test_current_book_labs_are_not_legacy(self) -> None:
        assert not contexts.is_legacy_lab_title("Virtual Lab 1-1, Guided: Configure Permissions")
        assert not contexts.is_legacy_lab_title(
            "U01A03: Virtual Lab 1-1 (Guided) — Configure Permissions"
        )

    def test_policy_can_replace_the_legacy_family(self) -> None:
        old_kit = re.compile(r"old kit \d+", re.IGNORECASE)
        policy = contexts.AuditPolicy(legacy_title_patterns=(old_kit,))
        assert contexts.is_legacy_lab_title("Old Kit 4", policy)
        assert not contexts.is_legacy_lab_title("Live Lab 1-1 x", policy)


class TestGroupingAndSpanningTitles:
    """Grouping drives both the report and the verdict."""

    def test_one_book_groups_into_one_context(self) -> None:
        items = [
            make_item("Chapter", PUBLISHER_NEW_NODE),
            make_item("eTextbook", PUBLISHER_NEW_ETEXT),
            make_item("Technical Support", NEW_BOOK_SUPPORT_LAUNCH),
        ]
        assert len(contexts.group_items_by_context(items)) == 1

    def test_two_books_group_into_two_contexts_largest_first(self) -> None:
        items = [
            make_item("Chapter", PUBLISHER_OLD_NODE, module_name="Unit 02"),
            make_item("Chapter", PUBLISHER_NEW_NODE),
            make_item("Chapter 2", PUBLISHER_NEW_ETEXT),
        ]
        groups = contexts.group_items_by_context(items)
        assert [len(group.launches) for group in groups] == [2, 1]

    def test_same_title_in_two_books_is_reported(self) -> None:
        items = [
            make_item("U01R01: Chapter", PUBLISHER_NEW_NODE),
            make_item("U01R01: Chapter", PUBLISHER_OLD_NODE),
        ]
        assert list(contexts.find_titles_spanning_contexts(items)) == ["U01R01: Chapter"]

    def test_same_title_in_one_book_is_not_reported(self) -> None:
        items = [
            make_item("U01R01: Chapter", PUBLISHER_NEW_NODE),
            make_item("U01R01: Chapter", NEW_BOOK_SIBLING_NODE_LAUNCH),
        ]
        assert contexts.find_titles_spanning_contexts(items) == {}

    def test_group_counts_split_navigation_from_graded_work(self) -> None:
        items = [
            make_item("Reading", PUBLISHER_OLD_NODE, source="module item"),
            make_item("Live Lab 2-1 x", PUBLISHER_OLD_NODE, "Labs", source="assignment"),
        ]
        group = contexts.group_items_by_context(items)[0]
        assert (group.module_item_count, group.assignment_count) == (1, 1)
        assert group.legacy_lab_count == 1
        assert group.affected_groupings == ["Labs", "Unit 01"]


class TestRecordedCollection:
    """The graded surface must be audited, not just module items."""

    def test_only_lti_assignments_are_collected(self, canvas) -> None:
        collected = contexts.collect_external_tool_assignments(canvas, COURSE_ID)
        assert [item.item_id for item in collected] == [201]

    def test_records_are_labeled_as_assignments(self, canvas) -> None:
        collected = contexts.collect_external_tool_assignments(canvas, COURSE_ID)
        assert {item.source for item in collected} == {"assignment"}
        assert collected[0].module_name == "Assignment group: Labs"

    def test_only_external_tool_module_items_are_collected(self, canvas) -> None:
        collected = contexts.collect_external_tool_items(canvas, COURSE_ID)
        assert [item.item_id for item in collected] == [112, 113, 121]
        assert {item.source for item in collected} == {"module item"}

    def test_shortened_live_lab_name_is_still_legacy(self, canvas) -> None:
        collected = contexts.collect_external_tool_items(canvas, COURSE_ID)
        assert [item.title for item in collected if item.is_legacy_lab_title] == [
            "Live Lab 1-1 Old Style"
        ]


class TestAuditTextContexts:
    """End to end over the recorded course."""

    def test_split_course_is_not_clean(self, canvas) -> None:
        report = contexts.audit_text_contexts(canvas, COURSE_ID)
        assert not report.is_clean
        assert report.context_count == 2
        assert report.module_item_launch_count == 3
        assert report.assignment_launch_count == 1
        assert report.total_launches == 4

    def test_lecture_tool_is_reported_but_not_counted(self, canvas) -> None:
        report = contexts.audit_text_contexts(canvas, COURSE_ID)
        assert [group.fingerprint for group in report.tool_contexts] == [
            "school.hosted.panopto.com/Panopto/LTI/LTI.aspx"
        ]
        assert report.context_count == 2  # the tool did not become a third book

    def test_single_book_course_is_clean(self, canvas, recorded_canvas) -> None:
        recorded_canvas.routes["/api/v1/courses/101/modules/12/items"] = []
        report = contexts.audit_text_contexts(canvas, COURSE_ID)
        assert report.is_clean
        assert report.context_count == 1

    def test_course_without_launches_is_clean_and_empty(self, canvas, recorded_canvas) -> None:
        recorded_canvas.routes["/api/v1/courses/101/modules"] = []
        recorded_canvas.routes["/api/v1/courses/101/assignments"] = []
        report = contexts.audit_text_contexts(canvas, COURSE_ID)
        assert report.is_clean
        assert report.total_launches == 0
