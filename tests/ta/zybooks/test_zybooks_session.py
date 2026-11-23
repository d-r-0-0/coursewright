"""Browser capture tests use mocks and synthetic reports only."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from coursewright.ta.zybooks.models import RetrievalRequest
from coursewright.ta.zybooks.session import (
    SIGN_IN_URL,
    ZyBooksAccessError,
    ZyBooksBrowserSession,
    ZyBooksReportNotReady,
)


@pytest.fixture
def browser_setup(tmp_path):
    playwright = MagicMock()
    browser = playwright.chromium.launch.return_value
    page = browser.new_context.return_value.new_page.return_value
    page.url = "https://learn.zybooks.com/#/zybook/example/reporting"
    page.get_by_role.return_value.count.return_value = 1
    download = page.expect_download.return_value.__enter__.return_value.value
    report_path = tmp_path / "report.csv"
    report_path.write_bytes(b"Section,Score\nExample,80\n")
    download.path.return_value = report_path
    factory = MagicMock()
    factory.return_value.start.return_value = playwright
    with patch.dict("sys.modules", {"playwright.sync_api": MagicMock(sync_playwright=factory)}):
        yield playwright, browser, page, download


def test_fetch_reuses_context_and_deletes_download(browser_setup):
    playwright, browser, page, download = browser_setup
    with ZyBooksBrowserSession() as session:
        assert session.fetch_report() == b"Section,Score\nExample,80\n"
        session.fetch_report()
        browser.new_context.assert_called_once_with(accept_downloads=True)
        page.goto.assert_called_once_with(
            SIGN_IN_URL, wait_until="domcontentloaded", timeout=60_000
        )
        page.get_by_role.assert_called_with("button", name="Download report", exact=True)
        assert download.delete.call_count == 2
    browser.close.assert_called_once()
    playwright.stop.assert_called_once()


def test_open_failure_closes_browser_without_leaking_details(browser_setup):
    playwright, browser, page, _download = browser_setup
    page.goto.side_effect = RuntimeError("SECRET")
    with (
        pytest.raises(ZyBooksAccessError, match="Could not open") as caught,
        ZyBooksBrowserSession(),
    ):
        pytest.fail("Navigation failed")
    assert "SECRET" not in str(caught.value)
    assert caught.value.__suppress_context__
    browser.close.assert_called_once()
    playwright.stop.assert_called_once()


@pytest.mark.parametrize("report_bytes", [b"", b"a" * 11])
def test_invalid_download_is_deleted(browser_setup, report_bytes):
    _playwright, _browser, _page, download = browser_setup
    download.path.return_value.write_bytes(report_bytes)
    with (
        patch("coursewright.ta.zybooks.session.MAX_REPORT_BYTES", 10),
        ZyBooksBrowserSession() as session,
        pytest.raises(ZyBooksAccessError, match="empty or exceeds"),
    ):
        session.fetch_report()
    download.delete.assert_called_once()


def test_download_failure_is_sanitized_and_cleaned(browser_setup):
    _playwright, _browser, _page, download = browser_setup
    download.path.side_effect = RuntimeError("SECRET")
    with (
        ZyBooksBrowserSession() as session,
        pytest.raises(ZyBooksAccessError, match="Report retrieval failed") as caught,
    ):
        session.fetch_report()
    assert "SECRET" not in str(caught.value)
    download.delete.assert_called_once()


def test_wrong_origin_never_clicks(browser_setup):
    _playwright, _browser, page, _download = browser_setup
    page.url = "https://learn.zybooks.com.example.org/"
    with (
        ZyBooksBrowserSession() as session,
        pytest.raises(ZyBooksAccessError, match="Reporting page"),
    ):
        session.fetch_report()
    page.get_by_role.assert_not_called()


def test_report_click_failure_does_not_retry(browser_setup):
    _playwright, _browser, page, _download = browser_setup
    page.get_by_role.return_value.click.side_effect = RuntimeError("SECRET")
    with (
        ZyBooksBrowserSession() as session,
        pytest.raises(ZyBooksAccessError, match="no retry"),
    ):
        session.fetch_report()
    page.get_by_role.return_value.click.assert_called_once()


@pytest.mark.parametrize(
    "count, visible, enabled",
    [(0, False, False), (2, True, True), (1, False, True), (1, True, False)],
)
def test_not_ready_keeps_browser_for_manual_navigation(browser_setup, count, visible, enabled):
    playwright, browser, page, _download = browser_setup
    report_button = page.get_by_role.return_value
    report_button.count.return_value = count
    report_button.is_visible.return_value = visible
    report_button.is_enabled.return_value = enabled
    with ZyBooksBrowserSession() as session:
        with pytest.raises(ZyBooksReportNotReady, match="open your zyBook first"):
            session.fetch_report()
        page.expect_download.assert_not_called()
        report_button.click.assert_not_called()
        browser.close.assert_not_called()
        playwright.stop.assert_not_called()
        report_button.count.return_value = 1
        report_button.is_visible.return_value = True
        report_button.is_enabled.return_value = True
        assert session.fetch_report() == b"Section,Score\nExample,80\n"
    browser.close.assert_called_once()


def test_cleanup_failure_closes_session(browser_setup):
    playwright, browser, _page, download = browser_setup
    download.delete.side_effect = RuntimeError("SECRET")
    with ZyBooksBrowserSession() as session:
        with pytest.raises(ZyBooksAccessError, match="cleanup failed"):
            session.fetch_report()
        with pytest.raises(ZyBooksAccessError, match="Open the zyBooks browser"):
            session.fetch_report()
    browser.close.assert_called_once()
    playwright.stop.assert_called_once()


def test_fetch_requires_open_session():
    with pytest.raises(ZyBooksAccessError, match="Open the zyBooks browser"):
        ZyBooksBrowserSession().fetch_report()


def test_missing_playwright_has_actionable_error():
    with (
        patch.dict("sys.modules", {"playwright.sync_api": None}),
        pytest.raises(ZyBooksAccessError, match="browser extra"),
        ZyBooksBrowserSession(),
    ):
        pytest.fail("Playwright is unavailable")


@pytest.mark.parametrize("book_id", ["", "../book", "book?token=x", "https://example.org"])
def test_retrieval_scope_rejects_urls_and_path_injection(book_id):
    with pytest.raises(ValueError, match="zyBook ID"):
        RetrievalRequest(book_id, (12,), datetime.now(UTC))


@pytest.mark.parametrize("section_ids", [(), (1, 1), (0,), (-1,), (True,), ("1",), [1]])
def test_retrieval_scope_requires_distinct_stable_section_ids(section_ids):
    with pytest.raises(ValueError, match="section IDs"):
        RetrievalRequest("ExampleBook", section_ids, datetime.now(UTC))


def test_retrieval_scope_rejects_ambiguous_local_time():
    with pytest.raises(ValueError, match="timezone"):
        RetrievalRequest("ExampleBook", (12,), datetime(2026, 9, 8, 12, 30))


def test_retrieval_scope_preserves_explicit_cutoff():
    cutoff = datetime(2026, 9, 8, 16, 22, tzinfo=UTC)
    request = RetrievalRequest("ExampleBook", (12, 19), cutoff)
    assert request.cutoff == cutoff
    assert request.section_ids == (12, 19)


def test_retrieval_scope_rejects_silent_cutoff_rounding():
    with pytest.raises(ValueError, match="minute precision"):
        RetrievalRequest("ExampleBook", (12,), datetime.now(UTC).replace(second=13))


@pytest.fixture
def automatic_setup(browser_setup):
    _playwright, _browser, page, download = browser_setup
    request = RetrievalRequest("ExampleBook", (12,), datetime(2026, 9, 8, tzinfo=UTC))
    listeners = {}
    page.on.side_effect = lambda event, callback: listeners.update({event: callback})
    page.locator.return_value.count.return_value = 0
    root = "https://zyserver.zybooks.com"
    export_url = root + "/v1/zybook/ExampleBook/activities/export"
    job_url = root + "/v1/status/unique-job"
    filename = "ExampleBook_report_42_2026-09-08_0000_UTC.csv"
    download.url = "https://cdn.zybooks.com/activity_reports/" + filename + "?signature=secret"
    route = MagicMock()
    route.request.method = "POST"
    route.request.post_data_json = {
        "time_zone_abbreviation": "UTC",
        "time_zone_offset": 0,
        "end_date": "2026-09-08T00:00:00.000Z",
        "sections": "[12]",
        "include_time_spent": False,
        "assignment_id": None,
    }

    def respond(url, data, status=200):
        response = MagicMock(url=url, status=status)
        response.json.return_value = data
        listeners["response"](response)
        listeners["requestfinished"](response.request)

    def navigate(*_args, **_kwargs):
        for name in ("ordering", "roster", "activity_information"):
            respond(root + "/v1/zybook/ExampleBook/" + name, {"success": True})

    def generate():
        page.route.call_args.args[1](route)
        if route.abort.called:
            return
        respond(export_url, {"success": True, "location": job_url}, 202)
        respond(job_url, {"success": True, "state": "PENDING"})
        respond(
            job_url,
            {
                "success": True,
                "state": "SUCCESS",
                "activity_data_report_id": 42,
                "name": filename,
            },
        )
        respond(
            root + "/v1/activity_report/42/download_url",
            {
                "success": True,
                "url": download.url,
            },
        )
        listeners["download"](download)

    page.goto.side_effect = navigate
    page.get_by_role.return_value.click.side_effect = lambda **_kwargs: generate()
    session = ZyBooksBrowserSession()
    session._page = page
    with patch.object(session, "_set_report_controls"):
        yield session, request, page, route, respond, listeners, download


def test_automatic_retrieval_binds_download_and_cleans_it(automatic_setup):
    session, request, page, route, _respond, _listeners, download = automatic_setup
    progress = MagicMock()
    result = session.retrieve_report(request, progress)
    assert result.request == request
    assert result.report_id == 42
    assert result.report_bytes == b"Section,Score\nExample,80\n"
    assert "Example,80" not in repr(result)
    route.continue_.assert_called_once()
    download.delete.assert_called_once()
    page.unroute.assert_called_once()
    assert progress.call_count >= 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("sections", "[12,19]"),
        ("sections", "[19]"),
        ("end_date", "2026-09-08T23:59:00Z"),
        ("include_time_spent", True),
        ("assignment_id", 1),
        ("start_date", "2026-09-01T00:00:00Z"),
    ],
)
def test_generation_guard_aborts_wrong_effective_settings(automatic_setup, field, value):
    session, request, _page, route, _respond, _listeners, _download = automatic_setup
    route.request.post_data_json[field] = value
    with pytest.raises(ZyBooksAccessError, match="Effective report settings"):
        session.retrieve_report(request)
    route.abort.assert_called_once()
    route.continue_.assert_not_called()


@pytest.mark.parametrize("status", [401, 403, 429])
def test_automatic_retrieval_stops_access_failures(automatic_setup, status):
    session, request, page, route, respond, _listeners, _download = automatic_setup
    page.goto.side_effect = lambda *_a, **_k: respond(
        "https://zyserver.zybooks.com/v1/zybook/ExampleBook/roster",
        {},
        status,
    )
    with pytest.raises(ZyBooksAccessError, match="no retry"):
        session.retrieve_report(request)
    route.continue_.assert_not_called()


def test_automatic_cancellation_does_not_generate(automatic_setup):
    session, request, page, route, _respond, _listeners, _download = automatic_setup
    with pytest.raises(ZyBooksAccessError, match="cancelled"):
        session.retrieve_report(request, cancelled=lambda: True)
    route.continue_.assert_not_called()
    page.unroute.assert_called_once()


def test_automatic_timeout_does_not_generate(automatic_setup):
    session, request, _page, route, _respond, _listeners, _download = automatic_setup
    with pytest.raises(ZyBooksAccessError, match="timed out"):
        session.retrieve_report(request, timeout_seconds=0)
    route.continue_.assert_not_called()


def test_unrelated_download_is_rejected_and_deleted(automatic_setup):
    session, request, _page, _route, _respond, listeners, download = automatic_setup
    original_capture = None

    def capture_wrong_download(event, callback):
        nonlocal original_capture
        if event == "download":
            original_capture = callback

            def wrong_report(item):
                item.url = "https://cdn.zybooks.com/activity_reports/old.csv"
                original_capture(item)

            listeners[event] = wrong_report
        else:
            listeners[event] = callback

    _page.on.side_effect = capture_wrong_download
    with pytest.raises(ZyBooksAccessError, match="did not match"):
        session.retrieve_report(request)
    download.delete.assert_called_once()


def test_query_roster_and_cached_ordering_are_supported(automatic_setup):
    session, request, page, _route, respond, _listeners, _download = automatic_setup
    root = "https://zyserver.zybooks.com/v1/zybook/ExampleBook/"

    def navigate(*_args, **_kwargs):
        respond(root + "roster?zybook_roles=Student", {"success": True})
        respond(root + "activity_information", {"success": True})

    page.goto.side_effect = navigate
    fallback = page.request.get.return_value
    fallback.status = 200
    fallback.json.return_value = {"success": True, "ordering": {}}

    # MagicMock.headers is not a string, so explicitly model a request header.
    def register(event, callback):
        if event == "response":

            def authorized(response):
                response.request.headers = {"authorization": "synthetic-provider-token"}
                callback(response)

            _listeners[event] = authorized
        else:
            _listeners[event] = callback

    page.on.side_effect = register
    result = session.retrieve_report(request)
    assert result.report_id == 42
    page.request.get.assert_called_once()
    assert page.request.get.call_args.args[0] == root + "ordering"
    fallback.dispose.assert_called_once()
