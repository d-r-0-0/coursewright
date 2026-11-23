"""Tests for the Panopto URL helpers.

The regression that matters here is HTML-escaped embed URLs. Canvas page
bodies store `&amp;` between query parameters, so a naive `parse_qs`
reads the session parameter as `amp;id` and reports no session at all.
That failure mode is dangerous rather than merely wrong: it makes a
perfectly good video look deleted, and the obvious next step is to
"clean up" content that was never broken.
"""

from __future__ import annotations

from coursewright.design.media.panopto_api import (
    parse_panopto_session_id,
    sanitize_filename_component,
)

SESSION_ID = "00000000-0000-0000-0000-000000000001"
EMBED_BASE = "https://panopto.example.edu/Panopto/Pages/Embed.aspx"


class TestParsePanoptoSessionId:
    """A session id must survive whatever shape the URL arrives in."""

    def test_plain_embed_url(self) -> None:
        url = f"{EMBED_BASE}?id={SESSION_ID}&autoplay=false"
        assert parse_panopto_session_id(url) == SESSION_ID

    def test_html_escaped_blackboard_url(self) -> None:
        url = f"{EMBED_BASE}?instance=bb&amp;id={SESSION_ID}&amp;v=1"
        assert parse_panopto_session_id(url) == SESSION_ID

    def test_id_is_not_the_first_parameter(self) -> None:
        url = f"{EMBED_BASE}?instance=bb&id={SESSION_ID}"
        assert parse_panopto_session_id(url) == SESSION_ID

    def test_viewer_url(self) -> None:
        url = f"https://panopto.example.edu/Panopto/Pages/Viewer.aspx?id={SESSION_ID}"
        assert parse_panopto_session_id(url) == SESSION_ID

    def test_url_without_a_session_returns_none(self) -> None:
        assert parse_panopto_session_id(f"{EMBED_BASE}?instance=bb") is None


class TestSanitizeFilenameComponent:
    """Session titles become filenames, so they must be safe."""

    def test_spaces_and_punctuation_are_replaced(self) -> None:
        assert sanitize_filename_component("Example: Unit 1!") == "Example__Unit_1"

    def test_empty_input_still_yields_a_name(self) -> None:
        assert sanitize_filename_component("///") == "untitled"
