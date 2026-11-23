"""Detect whether a course launches publisher content into several contexts.

Library version of COURSE_BUILDER ``cantrips/audit_text_contexts.py``.

Two publisher products can look identical in the Canvas UI: same titles,
same icon, same module, no error on click.  They differ only in the
context segment of the external-tool launch URL, and the only
student-visible symptom is "I bought the book but the lab says I have no
access."  A course that lived through an e-textbook edition change can
contain items from two books.

This groups every publisher launch by its context and returns the groups.
One group means the course is clean; two or more means the smaller or
older group is the retirement list.  It reads BOTH surfaces, because an
edition change can leave one clean while the other is still split:

* ExternalTool module items: the navigation surface;
* assignments with an external-tool submission: the graded surface.

Read-only.  Institution-specific values of the source (the school's tool
hosts) are now part of an injectable ``AuditPolicy``.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from coursewright.design.diagnostics.models import (
    ContextAuditReport,
    ContextGroup,
    ExternalToolLaunch,
)
from coursewright.design.diagnostics.reading import ReadOnlyCanvas, get_raw_assignments

# A gateway launch URL has a stable shape:
#
#   /rest/<launchType>/<accountId>/<productContextId>/<resource...>
#
# Every item in one e-textbook shares <accountId>/<productContextId>; a new
# edition changes <productContextId>.  Everything right of it is per-item
# noise (node ids, resource ids, "start" verbs).
#
#   .../launchBasicLTI/1000001/2000001_1000001_9001/3000001/nodeId/11
#   .../launchBasicLTI/1000001/2000001_1000001_9001/3000002
#   .../launchLTISupportTool/1000001/2000001_1000001_9001
#
# All three are the SAME book and must fingerprint identically, even though
# only the first carries a "nodeId" marker.  An earlier version truncated to
# a fixed segment count when no marker appeared, which split one book into
# three phantom contexts and, far worse, collapsed the OLD book's
# product-level launcher into the NEW book's prefix, hiding a real finding.
LAUNCH_TYPE_SEGMENT_PATTERN = re.compile(r"^launch", re.IGNORECASE)

# Segments that carry no identity and sit ahead of the ids we want.
IGNORED_LEADING_SEGMENTS = ("rest", "api", "v1")

# Segments after the launch-type marker that identify the product: the
# account id and the product context id.
PRODUCT_IDENTIFIER_SEGMENT_COUNT = 2

# Path segments whose value changes per *item* rather than per *book*.
# Used only by the generic fallback for hosts we have no shape for.
PER_ITEM_PATH_MARKERS = ("nodeId", "resource", "content", "launch", "item")

# Hosts that serve instructor-authored or institutional tools rather than
# publisher content.  A second *tool* is normal (a lecture-capture tool and
# a publisher lab are supposed to differ), so these never fail the audit.
# The list is an allowlist on purpose: anything NOT named counts as
# publisher content, so an unrecognised second publisher fails loudly
# instead of hiding in an "other" bucket.
DEFAULT_NON_PUBLISHER_HOSTS = frozenset({"www.youtube.com", "youtube.com"})
DEFAULT_NON_PUBLISHER_HOST_SUFFIXES = (".hosted.panopto.com", ".instructure.com")

# A retiring numbered lab family ships numbered labs plus one course-level
# prerequisite item, under two names because the publisher shortened the
# label mid-transition:
#
#   "Live Virtual Machine Lab 8-2: Securing Computing Resources"
#   "Live Lab 1-1 - Threat Vectors and Attack Surfaces"
#
# Searching only the long name misses the renamed survivors.  The lab number
# is required so orientation items that merely *mention* the lab environment
# ("Live Virtual Machine Lab Preparation") are not flagged, and current-book
# labs ("Virtual Lab 1-1, Guided", no leading "Live") do not match either.
DEFAULT_LEGACY_LAB_TITLE_PATTERNS = (
    re.compile(r"live\s+(?:virtual\s+machine\s+|vm\s+)?lab\s*\d+\s*-\s*\d+", re.IGNORECASE),
    # The one unnumbered member of the family, matched exactly.
    re.compile(r"^pre-?requisite:\s*live\s+virtual\s+machine\s+labs\s*$", re.IGNORECASE),
)


@dataclass(frozen=True)
class AuditPolicy:
    """What counts as a tool host and what counts as a retiring title."""

    non_publisher_hosts: Collection[str] = DEFAULT_NON_PUBLISHER_HOSTS
    non_publisher_host_suffixes: Sequence[str] = DEFAULT_NON_PUBLISHER_HOST_SUFFIXES
    legacy_title_patterns: Sequence[re.Pattern[str]] = DEFAULT_LEGACY_LAB_TITLE_PATTERNS

    def with_canvas_host(self, canvas_base_url: str) -> AuditPolicy:
        """Return a policy that also treats the Canvas host itself as a tool."""
        canvas_host = urlparse(canvas_base_url).netloc.lower()
        if not canvas_host:
            return self
        return AuditPolicy(
            non_publisher_hosts=frozenset(self.non_publisher_hosts) | {canvas_host},
            non_publisher_host_suffixes=self.non_publisher_host_suffixes,
            legacy_title_patterns=self.legacy_title_patterns,
        )


DEFAULT_AUDIT_POLICY = AuditPolicy()


def is_legacy_lab_title(title: str, policy: AuditPolicy = DEFAULT_AUDIT_POLICY) -> bool:
    """True for members of the retiring numbered lab family."""
    stripped_title = title.strip()
    return any(pattern.search(stripped_title) for pattern in policy.legacy_title_patterns)


def is_publisher_launch(launch_url: str, policy: AuditPolicy = DEFAULT_AUDIT_POLICY) -> bool:
    """True when a launch URL points at publisher course content.

    Fail-closed: an unknown host is treated as a publisher, because a
    missed second publisher is the failure this audit exists to prevent.
    A misfiled instructor tool merely gets reported.
    """
    if not launch_url:
        return False
    host = urlparse(launch_url).netloc.lower()
    if host in {name.lower() for name in policy.non_publisher_hosts}:
        return False
    return not any(host.endswith(suffix.lower()) for suffix in policy.non_publisher_host_suffixes)


def extract_product_identifier_segments(path_segments: list[str]) -> list[str]:
    """Pull the account and product ids out of a gateway launch path.

    Returns an empty list when the path has no launch-type marker, which
    tells the caller to use the generic fallback instead.

    Example:
        ["rest", "launchBasicLTI", "1000001", "2000001_...", "3000001"]
        -> ["1000001", "2000001_..."]
    """
    remaining_segments = list(path_segments)

    while remaining_segments and remaining_segments[0].lower() in IGNORED_LEADING_SEGMENTS:
        remaining_segments.pop(0)

    if not remaining_segments:
        return []
    if not LAUNCH_TYPE_SEGMENT_PATTERN.match(remaining_segments[0]):
        return []

    # Drop the launch-type segment itself: two launch verbs into the same
    # book ("launchBasicLTI", "launchLTISupportTool") must not read as two
    # different books.
    remaining_segments.pop(0)
    return remaining_segments[:PRODUCT_IDENTIFIER_SEGMENT_COUNT]


def build_generic_fingerprint(netloc: str, path_segments: list[str]) -> str:
    """Fingerprint a launch URL whose shape we do not recognise.

    Trims per-item noise where a known marker appears and otherwise keeps
    the whole path.  Keeping too much splits one product into several
    reported contexts, which a human reconciles: the safe direction to be
    wrong in.
    """
    markers = {marker.lower() for marker in PER_ITEM_PATH_MARKERS}
    context_segments: list[str] = []
    for segment in path_segments:
        if segment.lower() in markers:
            break
        context_segments.append(segment)
    return "/".join([netloc, *context_segments])


def build_context_fingerprint(launch_url: str) -> str:
    """Reduce an LTI launch URL to a key stable across items of one product.

    These three collapse to one fingerprint, because they are one book:
        .../launchBasicLTI/1000001/2000001_.../3000001/nodeId/11
        .../launchBasicLTI/1000001/2000001_.../3000002
        .../launchLTISupportTool/1000001/2000001_...
        -> "gateway.publisher.example/1000001/2000001_..."

    while the previous edition does not, which is the signal we want.
    """
    if not launch_url:
        return "(no launch url)"

    parsed_url = urlparse(launch_url)
    path_segments = [segment for segment in parsed_url.path.split("/") if segment]

    product_identifier_segments = extract_product_identifier_segments(path_segments)
    if product_identifier_segments:
        return "/".join([parsed_url.netloc, *product_identifier_segments])

    return build_generic_fingerprint(parsed_url.netloc, path_segments)


def collect_external_tool_items(
    canvas: ReadOnlyCanvas,
    course_id: int,
    policy: AuditPolicy = DEFAULT_AUDIT_POLICY,
) -> list[ExternalToolLaunch]:
    """Walk every module and return one record per ExternalTool item.

    Each record keeps the module name so the result tells you which units
    are affected, which turns the finding into a work list.
    """
    launches: list[ExternalToolLaunch] = []

    for module in canvas.get_modules(course_id):
        module_name = module.get("name", "(unnamed module)")

        for item in canvas.get_module_items(course_id, module["id"]):
            if item.get("type") != "ExternalTool":
                continue

            launch_url = item.get("external_url", "")
            title = item.get("title", "(untitled)")
            launches.append(
                ExternalToolLaunch(
                    source="module item",
                    module_name=module_name,
                    item_id=item.get("id"),
                    title=title,
                    launch_url=launch_url,
                    context=build_context_fingerprint(launch_url),
                    is_publisher_launch=is_publisher_launch(launch_url, policy),
                    is_legacy_lab_title=is_legacy_lab_title(title, policy),
                )
            )

    return launches


def collect_external_tool_assignments(
    canvas: ReadOnlyCanvas,
    course_id: int,
    policy: AuditPolicy = DEFAULT_AUDIT_POLICY,
) -> list[ExternalToolLaunch]:
    """Return one record per assignment whose submission is an LTI launch.

    This is the half that matters most and is easiest to miss.  Publisher
    labs, simulations and quizzes are Canvas *assignments* with an
    external-tool submission, not ExternalTool module items, so an audit of
    module items alone reports a clean-looking course while most graded
    work still launches into the retired e-textbook.  In the source's
    measurement the assignments were both more numerous and more evenly
    split across the two books than the module items.
    """
    assignment_group_names = {
        group["id"]: group.get("name", "(unnamed group)")
        for group in canvas.get_assignment_groups(course_id)
    }

    launches: list[ExternalToolLaunch] = []

    for assignment in get_raw_assignments(canvas, course_id):
        tool_attributes = assignment.get("external_tool_tag_attributes") or {}
        launch_url = tool_attributes.get("url", "")
        if not launch_url:
            continue

        group_name = assignment_group_names.get(
            assignment.get("assignment_group_id"), "(no assignment group)"
        )
        title = assignment.get("name", "(untitled)")

        launches.append(
            ExternalToolLaunch(
                source="assignment",
                module_name=f"Assignment group: {group_name}",
                item_id=assignment.get("id"),
                title=title,
                launch_url=launch_url,
                context=build_context_fingerprint(launch_url),
                is_publisher_launch=is_publisher_launch(launch_url, policy),
                is_legacy_lab_title=is_legacy_lab_title(title, policy),
            )
        )

    return launches


def group_items_by_context(items: Sequence[ExternalToolLaunch]) -> list[ContextGroup]:
    """Bucket launches under their context fingerprint, largest first."""
    grouped: dict[str, list[ExternalToolLaunch]] = {}
    for item in items:
        grouped.setdefault(item.context, []).append(item)

    # Sorting by size puts the presumed-current product on top.  It is a
    # heuristic, not a verdict: the human confirms which is which.
    ordered = sorted(grouped.items(), key=lambda pair: len(pair[1]), reverse=True)
    return [ContextGroup(fingerprint=context, launches=launches) for context, launches in ordered]


def split_publisher_and_tool_items(
    items: Sequence[ExternalToolLaunch],
) -> tuple[list[ExternalToolLaunch], list[ExternalToolLaunch]]:
    """Separate publisher course content from instructor/institution tools.

    Only the publisher half is subject to the one-e-textbook rule.  A
    lecture tool launching from a different host than a publisher lab is
    normal and must not be reported as a second e-textbook.
    """
    publisher_items = [item for item in items if item.is_publisher_launch]
    tool_items = [item for item in items if not item.is_publisher_launch]
    return publisher_items, tool_items


def find_titles_spanning_contexts(items: Sequence[ExternalToolLaunch]) -> dict[str, list[str]]:
    """Return titles that appear under more than one context.

    These are the highest-risk items: the same reading or lab exists twice,
    so a student's experience depends on which copy they click.
    """
    contexts_per_title: dict[str, set[str]] = {}
    for item in items:
        contexts_per_title.setdefault(item.title, set()).add(item.context)

    return {
        title: sorted(contexts)
        for title, contexts in contexts_per_title.items()
        if len(contexts) > 1
    }


def audit_text_contexts(
    canvas: ReadOnlyCanvas,
    course_id: int,
    policy: AuditPolicy | None = None,
) -> ContextAuditReport:
    """Audit one course; ``report.is_clean`` is False when contexts are split.

    The Canvas instance's own host is always treated as a tool host, in
    addition to whatever *policy* names.
    """
    effective_policy = (policy or DEFAULT_AUDIT_POLICY).with_canvas_host(canvas.base_url)

    # Both halves matter: module items are the navigation surface, and
    # assignments are the graded surface.
    module_item_launches = collect_external_tool_items(canvas, course_id, effective_policy)
    assignment_launches = collect_external_tool_assignments(canvas, course_id, effective_policy)
    all_launches = module_item_launches + assignment_launches

    publisher_items, tool_items = split_publisher_and_tool_items(all_launches)

    return ContextAuditReport(
        course_id=course_id,
        total_launches=len(all_launches),
        module_item_launch_count=len(module_item_launches),
        assignment_launch_count=len(assignment_launches),
        publisher_contexts=group_items_by_context(publisher_items),
        tool_contexts=group_items_by_context(tool_items),
        titles_spanning_contexts=find_titles_spanning_contexts(publisher_items),
    )
