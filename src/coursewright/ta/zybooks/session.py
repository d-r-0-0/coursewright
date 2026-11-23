"""App-owned browser session; no credentials or browser state are persisted.

Playwright is an optional extra, imported lazily inside ``__enter__`` exactly
as ``core/auth/browser_login`` does, so importing this module (and the whole
``ta.zybooks`` package) works on an installation without the browser extra.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from coursewright.ta.zybooks.models import RetrievalRequest, RetrievedReport

SIGN_IN_URL = "https://learn.zybooks.com/#/signin"
MAX_REPORT_BYTES = 20 * 1024 * 1024
API_ORIGIN = "https://zyserver.zybooks.com"


class ZyBooksAccessError(RuntimeError):
    """A browser operation failed without exposing authenticated request details."""


class ZyBooksReportNotReady(ZyBooksAccessError):
    """The current page has no usable report control; keep the browser open."""


class ZyBooksBrowserSession:
    """Own a synchronous browser on one thread, reusing login until close.

    The instructor logs in and selects Reporting settings in the visible browser.
    No authentication cookies are guessed and no Canvas credentials are used.
    """

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    def __enter__(self) -> ZyBooksBrowserSession:
        if self._playwright is not None:
            raise ZyBooksAccessError("This browser session is already open.")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ZyBooksAccessError(
                "Install the browser extra and Chromium before connecting to zyBooks."
            ) from None

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=False)
            context = self._browser.new_context(accept_downloads=True)
            self._page = context.new_page()
            self._page.goto(SIGN_IN_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            self.close()
            raise ZyBooksAccessError(
                "Could not open zyBooks. Check connectivity and the Chromium installation."
            ) from None
        return self

    def __exit__(self, *exception_details: object) -> None:
        self.close()

    def close(self) -> None:
        """Release browser-owned temporary downloads and in-memory login state."""
        try:
            if self._browser is not None:
                with suppress(Exception):
                    self._browser.close()
        finally:
            if self._playwright is not None:
                with suppress(Exception):
                    self._playwright.stop()
            self._page = None
            self._browser = None
            self._playwright = None

    def retrieve_report(
        self,
        request: RetrievalRequest,
        progress: Callable[[str], None] = lambda _message: None,
        cancelled: Callable[[], bool] = lambda: False,
        timeout_seconds: float = 120,
    ) -> RetrievedReport:
        """Automate the observed September 2026 instructor report workflow.

        Private response schemas are deliberately isolated here. The browser
        owns authentication and downloads; signed URLs are never replayed.
        A route guard checks the actual outgoing scope before allowing ONE job.
        """
        if self._page is None:
            raise ZyBooksAccessError("Open the zyBooks browser before fetching a report.")
        page = self._page
        deadline = monotonic() + timeout_seconds
        metadata: dict[str, dict] = {}
        job: dict[str, Any] = {}
        failures: list[str] = []
        downloads: list[Any] = []
        pending_responses: list[Any] = []
        finished_requests: set[Any] = set()
        authorization: list[str] = []
        succeeded = False
        export_url = f"{API_ORIGIN}/v1/zybook/{request.book_id}/activities/export"
        metadata_prefix = f"{API_ORIGIN}/v1/zybook/{request.book_id}/"

        def check_running() -> None:
            ready = [
                response for response in pending_responses if response.request in finished_requests
            ]
            for response in ready:
                pending_responses.remove(response)
                parse_response(response)
            if cancelled():
                raise ZyBooksAccessError("Retrieval cancelled; no Canvas grades were changed.")
            if failures:
                raise ZyBooksAccessError(failures[0])
            if monotonic() >= deadline:
                raise ZyBooksAccessError(
                    "Report retrieval timed out; no duplicate job was created."
                )

        def observe_response(response: Any) -> None:
            if urlsplit(response.url).hostname in {"zyserver.zybooks.com", "cdn.zybooks.com"}:
                pending_responses.append(response)

        def request_finished(completed_request: Any) -> None:
            finished_requests.add(completed_request)

        def parse_response(response: Any) -> None:
            # Never put request headers, signed URLs or source payloads in errors.
            parsed = urlsplit(response.url)
            if parsed.hostname not in {"zyserver.zybooks.com", "cdn.zybooks.com"}:
                return
            if response.status in {401, 403, 429}:
                failures.append(
                    "zyBooks denied access or rate limited retrieval; no retry was made."
                )
                return
            try:
                suffix = response.url.split("?", 1)[0].removeprefix(metadata_prefix)
                if suffix in {"ordering", "roster", "activity_information"}:
                    body = response.json()
                    if response.status != 200 or body.get("success") is not True:
                        raise ValueError
                    metadata[suffix] = body
                    header = response.request.headers.get("authorization")
                    if isinstance(header, str) and header:
                        authorization[:] = [header]
                elif response.url == export_url:
                    body = response.json()
                    location = body.get("location", "")
                    target = urlsplit(location)
                    if (
                        response.status != 202
                        or body.get("success") is not True
                        or target.scheme != "https"
                        or target.netloc != "zyserver.zybooks.com"
                        or not target.path.startswith("/v1/status/")
                        or target.query
                        or target.fragment
                        or "location" in job
                    ):
                        raise ValueError
                    job["location"] = location
                elif response.url == job.get("location"):
                    body = response.json()
                    if response.status != 200 or body.get("success") is not True:
                        raise ValueError
                    if body.get("state") == "SUCCESS":
                        report_id = body.get("activity_data_report_id")
                        name = body.get("name")
                        if type(report_id) is not int or report_id <= 0:
                            raise ValueError
                        if (
                            not isinstance(name, str)
                            or not name.startswith(f"{request.book_id}_report_{report_id}_")
                            or not name.endswith(".csv")
                            or "/" in name
                            or "\\" in name
                        ):
                            raise ValueError
                        job.update(report_id=report_id, name=name)
                    elif body.get("state") not in {"PENDING", "STARTED", "PROGRESS"}:
                        raise ValueError
                elif "report_id" in job and response.url == (
                    f"{API_ORIGIN}/v1/activity_report/{job['report_id']}/download_url"
                ):
                    body = response.json()
                    target = urlsplit(body.get("url", ""))
                    if (
                        response.status != 200
                        or body.get("success") is not True
                        or target.scheme != "https"
                        or target.netloc != "cdn.zybooks.com"
                        or target.path != f"/activity_reports/{job['name']}"
                    ):
                        raise ValueError
                    job["download_url"] = body["url"]
            except Exception:
                failures.append("Unsupported zyBooks response schema; no grades were parsed.")

        def guard_generation(route: Any) -> None:
            try:
                outgoing = route.request.post_data_json
                actual_cutoff = datetime.fromisoformat(outgoing["end_date"].replace("Z", "+00:00"))
                actual_sections = json.loads(outgoing["sections"])
                if (
                    route.request.method != "POST"
                    or job.get("submitted")
                    or set(outgoing)
                    != {
                        "time_zone_abbreviation",
                        "time_zone_offset",
                        "end_date",
                        "sections",
                        "include_time_spent",
                        "assignment_id",
                    }
                    or actual_cutoff != request.cutoff.astimezone(UTC)
                    or len(actual_sections) != len(request.section_ids)
                    or set(actual_sections) != set(request.section_ids)
                    or outgoing["include_time_spent"] is not False
                    or outgoing["assignment_id"] is not None
                ):
                    raise ValueError
                job["submitted"] = True
                route.continue_()
            except Exception:
                failures.append(
                    "Effective report settings did not match the request; generation stopped."
                )
                route.abort()

        def capture_download(download: Any) -> None:
            downloads.append(download)

        try:
            page.on("response", observe_response)
            page.on("requestfinished", request_finished)
            page.on("download", capture_download)
            page.route(export_url, guard_generation)
            progress("Opening Reporting and checking book metadata...")
            page.goto(
                f"https://learn.zybooks.com/zybook/{request.book_id}?selectedPanel=view-activity",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            while len(metadata) < 3:
                check_running()
                if page.locator('input[type="password"]').count():
                    raise ZyBooksAccessError("Log in in the app-owned browser before retrieving.")
                # The app may reuse cached ordering without issuing its GET.
                # Fetch the observed endpoint once with this browser's provider-
                # specific authorization, never a Canvas credential or disk state.
                if "ordering" not in metadata and authorization:
                    response = page.request.get(
                        metadata_prefix + "ordering",
                        headers={"authorization": authorization[0]},
                        timeout=max(1, min(10_000, int((deadline - monotonic()) * 1000))),
                    )
                    try:
                        if response.status in {401, 403, 429}:
                            raise ZyBooksAccessError(
                                "zyBooks denied access or rate limited retrieval; "
                                "no retry was made."
                            )
                        body = response.json()
                        if response.status != 200 or body.get("success") is not True:
                            raise ZyBooksAccessError("Unsupported zyBooks ordering response.")
                        metadata["ordering"] = body
                    finally:
                        response.dispose()
                page.wait_for_timeout(250)
            check_running()
            self._set_report_controls(request, metadata["ordering"], check_running)
            progress("Generating one report with the verified sections and cutoff...")
            page.get_by_role("button", name="Generate score report", exact=True).click(
                timeout=10_000
            )
            while not downloads or "download_url" not in job:
                check_running()
                progress("Waiting for this report's background job and browser download...")
                page.wait_for_timeout(250)
            check_running()
            if len(downloads) != 1 or downloads[0].url != job["download_url"]:
                raise ZyBooksAccessError("The browser download did not match this report job.")
            progress("Reading this report's temporary download...")
            download_path = downloads[0].path()
            if download_path is None:
                raise ZyBooksAccessError("The report download did not complete.")
            with Path(download_path).open("rb") as report_file:
                report_bytes = report_file.read(MAX_REPORT_BYTES + 1)
            check_running()
            if not report_bytes or len(report_bytes) > MAX_REPORT_BYTES:
                raise ZyBooksAccessError("The report is empty or exceeds the 20 MiB limit.")
            result = RetrievedReport(
                request,
                job["report_id"],
                report_bytes,
                metadata["ordering"],
                metadata["activity_information"],
                metadata["roster"],
            )
            succeeded = True
            return result
        except ZyBooksAccessError as error:
            raise error from None
        except Exception:
            raise ZyBooksAccessError("Automatic retrieval failed; no retry was made.") from None
        finally:
            authorization.clear()
            with suppress(Exception):
                page.remove_listener("response", observe_response)
                page.remove_listener("requestfinished", request_finished)
                page.remove_listener("download", capture_download)
                page.unroute(export_url, guard_generation)
            for download in downloads:
                try:
                    download.delete()
                except Exception:
                    self.close()
                    raise ZyBooksAccessError(
                        "Download cleanup failed; the browser was closed."
                    ) from None
            if not succeeded and job.get("submitted"):
                # A report may finish after cancellation. Closing its owner also
                # removes any late browser-owned temporary download.
                self.close()

    def _set_report_controls(
        self, request: RetrievalRequest, ordering: dict, check_running: Callable[[], None]
    ) -> None:
        """Select leaf sections, clear the lower bound, and verify visible values."""
        page = self._page
        chapters = ordering["ordering"]["content_ordering"]["chapters"]
        section_locations = {
            section["canonical_section_id"]: (chapter_index, chapter["number"], section["number"])
            for chapter_index, chapter in enumerate(chapters)
            for section in chapter["sections"]
        }
        if not set(request.section_ids) <= set(section_locations):
            raise ZyBooksAccessError("Requested section IDs are not in this book's ordering.")
        # A chapter checkbox is a bulk selector. Clear all prior selections and
        # select leaf checkboxes so partial units cannot expand to whole chapters.
        clear = page.get_by_text("Clear", exact=True)
        if clear.count() == 1 and clear.is_visible():
            clear.click(timeout=10_000)
        for section_id in request.section_ids:
            check_running()
            index, chapter_number, section_number = section_locations[section_id]
            chapter = page.locator(".chapter-item").nth(index)
            href = f"/zybook/{request.book_id}/chapter/{chapter_number}/section/{section_number}"
            section = chapter.locator(".section-item").filter(has=page.locator(f'a[href="{href}"]'))
            if not section.count() or not section.is_visible():
                chapter.get_by_role("button").click(timeout=10_000)
            section.get_by_role("checkbox").check(timeout=10_000)
        page.locator(".user-select-trigger").click(timeout=10_000)
        page.get_by_role("option", name="Entire class", exact=True).click(timeout=10_000)
        # Flatpickr exposes its public widget API on the observed readonly input.
        # Triggering change updates Ember; the outgoing-request guard verifies it.
        page.get_by_label("From date", exact=True).evaluate("e => e._flatpickr.clear()")
        local = page.evaluate(
            """iso => {
            const d = new Date(iso);
            const pad = n => String(n).padStart(2, '0');
            return {date: `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`,
                time: `${d.getHours()%12 || 12}:${pad(d.getMinutes())} `
                    + (d.getHours()<12 ? 'am' : 'pm')};
        }""",
            request.cutoff.isoformat(),
        )
        page.get_by_label("Until date", exact=True).evaluate(
            "(e, value) => e._flatpickr.setDate(value, true, 'Y-m-d')", local["date"]
        )
        page.get_by_role("combobox", name="Until time", exact=True).click(timeout=10_000)
        page.get_by_placeholder("Search", exact=True).fill(local["time"], timeout=10_000)
        page.get_by_role("option", name=local["time"], exact=True).click(timeout=10_000)
        page.locator(".include-time-spent-container input").uncheck(timeout=10_000)
        if page.get_by_label("From date", exact=True).input_value():
            raise ZyBooksAccessError("The lower date bound could not be cleared.")
        check_running()

    def fetch_report(self) -> bytes:
        """Click the standard report button and consume its temporary download.

        This discovery operation uses the current instructor-selected report
        settings. It does not yet certify scope, cutoff, or per-lab completeness.
        """
        if self._page is None:
            raise ZyBooksAccessError("Open the zyBooks browser before fetching a report.")

        download = None
        try:
            current_url = urlsplit(self._page.url)
            if current_url.scheme != "https" or current_url.hostname != "learn.zybooks.com":
                raise ZyBooksAccessError("Select the Reporting page in the zyBooks browser.")
            report_button = self._page.get_by_role("button", name="Download report", exact=True)
            if (
                report_button.count() != 1
                or not report_button.is_visible()
                or not report_button.is_enabled()
            ):
                raise ZyBooksReportNotReady(
                    "No usable Download report button on this page. "
                    "If you see book selection, open your zyBook first, then Reporting. "
                    "Select sections and the Until date/time. "
                    "The browser stays open; enter fetch when ready, or quit. "
                    "If Reporting is already open, the report control needs inspection."
                )
            with self._page.expect_download(timeout=120_000) as download_event:
                report_button.click(timeout=10_000)
            download = download_event.value
            download_path = download.path()
            if download_path is None:
                raise ZyBooksAccessError("The report download did not complete.")
            with Path(download_path).open("rb") as report_file:
                report_bytes = report_file.read(MAX_REPORT_BYTES + 1)
            if not report_bytes or len(report_bytes) > MAX_REPORT_BYTES:
                raise ZyBooksAccessError("The report is empty or exceeds the 20 MiB limit.")
            return report_bytes
        except ZyBooksAccessError as error:
            raise error from None
        except Exception:
            raise ZyBooksAccessError(
                "Report retrieval failed. Check login and Reporting settings; no retry was made."
            ) from None
        finally:
            if download is not None:
                try:
                    download.delete()
                except Exception:
                    self.close()
                    raise ZyBooksAccessError(
                        "Download cleanup failed; the browser was closed."
                    ) from None
