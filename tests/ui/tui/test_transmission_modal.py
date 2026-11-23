"""Lifecycle tests for the reusable TransmissionModal widget."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal


class TransmissionModalTestApp(App):
    """Tiny host app that can push a TransmissionModal on demand."""

    def compose(self) -> ComposeResult:
        yield Static("background")


@pytest.mark.asyncio
async def test_transmission_modal_push_and_spinner_present() -> None:
    app = TransmissionModalTestApp()
    async with app.run_test() as pilot:
        app.push_screen(TransmissionModal("Loading..."))
        await pilot.pause()
        assert isinstance(app.screen, TransmissionModal)
        spinner = app.screen.query_one("#transmission-spinner")
        assert spinner is not None


@pytest.mark.asyncio
async def test_transmission_modal_success_autodismiss() -> None:
    app = TransmissionModalTestApp()
    async with app.run_test() as pilot:
        modal = TransmissionModal("Posting...")
        app.push_screen(modal)
        await pilot.pause()
        app.pop_screen()
        await pilot.pause()
        assert not isinstance(app.screen, TransmissionModal)


@pytest.mark.asyncio
async def test_transmission_modal_error_stays_and_escape_dismisses() -> None:
    app = TransmissionModalTestApp()
    async with app.run_test() as pilot:
        modal = TransmissionModal("Posting...")
        app.push_screen(modal)
        await pilot.pause()
        modal.mark_error("Request failed")
        await pilot.pause()
        assert isinstance(app.screen, TransmissionModal)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TransmissionModal)


@pytest.mark.asyncio
async def test_transmission_modal_renders_published_traffic_event() -> None:
    """Events published to the traffic bus while the modal is mounted
    should land in the modal log so users see live API chatter."""
    from coursewright.core import traffic as traffic_bus

    traffic_bus.reset_for_tests()
    app = TransmissionModalTestApp()
    async with app.run_test() as pilot:
        modal = TransmissionModal("Loading courses...")
        app.push_screen(modal)
        await pilot.pause()

        traffic_bus.publish(
            traffic_bus.TrafficEvent(
                source="canvas",
                method="GET",
                target="courses",
                status=200,
                duration_ms=137,
            )
        )
        # call_from_thread schedules onto the event loop; let it run.
        for _ in range(20):
            await pilot.pause(0.02)
            if modal._log_lines:
                break

        assert modal._log_lines, "modal log should have received the event"
        assert "courses" in modal._log_lines[0]
        assert "200" in modal._log_lines[0]

    traffic_bus.reset_for_tests()


@pytest.mark.asyncio
async def test_transmission_modal_unsubscribes_on_unmount() -> None:
    """Once the modal is popped, further published events must not be
    delivered (no zombie subscriber holding a stale widget reference)."""
    from coursewright.core import traffic as traffic_bus

    traffic_bus.reset_for_tests()
    app = TransmissionModalTestApp()
    async with app.run_test() as pilot:
        modal = TransmissionModal("Loading...")
        app.push_screen(modal)
        await pilot.pause()
        app.pop_screen()
        await pilot.pause()

        log_lines_before = list(modal._log_lines)
        traffic_bus.publish(
            traffic_bus.TrafficEvent("canvas", "GET", "courses", 200, 5)
        )
        for _ in range(10):
            await pilot.pause(0.02)
        assert modal._log_lines == log_lines_before

    traffic_bus.reset_for_tests()
