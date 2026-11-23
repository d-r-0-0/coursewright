"""Tests for the Model Picker TUI Screen."""

from unittest.mock import MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable

from coursewright.core.config.settings import AppConfig
from coursewright.core.llm import LLMProvider
from coursewright.core.llm.model_registry import ModelMetadata
from coursewright.ui.tui.screens.model_picker import ModelPickerScreen
from coursewright.ui.tui.state import AppState


class ModelPickerApp(App):
    """Test app that loads the model picker screen."""

    def __init__(self, app_state: AppState | None = None):
        super().__init__()
        self.selected_model = None
        self.force_selection = False
        if app_state is not None:
            self.app_state = app_state

    def compose(self) -> ComposeResult:
        yield DataTable()  # Dummy widget

    def on_mount(self) -> None:
        self.push_screen(
            ModelPickerScreen(force_selection=self.force_selection),
            callback=self.on_model_selected,
        )

    def on_model_selected(self, model_name: str | None) -> None:
        self.selected_model = model_name


@pytest.fixture
def mock_models():
    """Mock list of available models."""
    return [
        ModelMetadata(
            name="claude-3-opus",
            provider=LLMProvider.ANTHROPIC,
            description="Powerful model",
            input_cost_per_1k=15.0,
            output_cost_per_1k=75.0,
            max_output_tokens=4096,
            speed="slow",
        ),
        ModelMetadata(
            name="gpt-4o",
            provider=LLMProvider.OPENAI,
            description="Fast model",
            input_cost_per_1k=5.0,
            output_cost_per_1k=15.0,
            max_output_tokens=4096,
            speed="standard",
        ),
    ]


@pytest.mark.asyncio
async def test_model_picker_load_and_display(mock_models):
    """Test that the screen loads and displays models correctly."""
    with (
        patch("coursewright.ui.tui.screens.model_picker.get_models", return_value=mock_models),
        patch("coursewright.ui.tui.screens.model_picker.AppConfig.load_from_file") as mock_load,
    ):
        # Setup mock config
        mock_config = MagicMock(spec=AppConfig)
        mock_config.llm = MagicMock()
        mock_config.llm.default_model = "gpt-4o"
        mock_load.return_value = mock_config

        app = ModelPickerApp()
        async with app.run_test() as pilot:
            # Wait for screen to push
            await pilot.pause()

            # Check if screen is active
            screen = app.screen
            assert isinstance(screen, ModelPickerScreen)

            # Check DataTable content
            table = screen.query_one(DataTable)
            assert table.row_count == 2

            # Check headers
            assert len(table.columns) == 5
            cols = list(table.columns.values())
            assert cols[0].label.plain == "Provider"
            assert cols[1].label.plain == "Model"

            # Check row content (provider, name, speed, input cost, output cost)
            # Row 1: Anthropic
            row1 = table.get_row("claude-3-opus")
            assert row1[0] == "anthropic"
            assert row1[1] == "claude-3-opus"
            assert row1[2] == "slow"
            assert row1[3] == "$15.0000"

            # Row 2: OpenAI
            row2 = table.get_row("gpt-4o")
            assert row2[0] == "openai"
            assert row2[1] == "gpt-4o"


@pytest.mark.asyncio
async def test_model_selection_updates_config(mock_models):
    """Test selecting a model updates the configuration."""
    with (
        patch("coursewright.ui.tui.screens.model_picker.get_models", return_value=mock_models),
        patch("coursewright.ui.tui.screens.model_picker.get_model") as mock_get_model,
        patch("coursewright.ui.tui.screens.model_picker.AppConfig.load_from_file") as mock_load,
    ):
        # Setup get_model side effect
        model_map = {m.name: m for m in mock_models}
        mock_get_model.side_effect = lambda name: model_map.get(name)

        # Setup mock config
        mock_config = MagicMock(spec=AppConfig)
        mock_config.llm = MagicMock()
        mock_config.llm.default_model = "gpt-4o"
        mock_load.return_value = mock_config

        app = ModelPickerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            table = screen.query_one(DataTable)

            # Find index of row
            row_index = table.get_row_index("claude-3-opus")
            table.move_cursor(row=row_index)

            # Press enter to select
            await pilot.press("enter")

            # Wait for dismissal
            await pilot.pause()

            # Check if config was updated
            mock_config.llm.remember_selection.assert_called_with("anthropic", "claude-3-opus")
            assert mock_config.llm.default_model == "claude-3-opus"
            assert mock_config.llm.default_provider == "anthropic"
            mock_config.save_to_file.assert_called_once()

            # Check return value
            assert app.selected_model == "claude-3-opus"


@pytest.mark.asyncio
async def test_forced_picker_highlights_saved_model(mock_models):
    with (
        patch("coursewright.ui.tui.screens.model_picker.get_models", return_value=mock_models),
        patch("coursewright.ui.tui.screens.model_picker.AppConfig.load_from_file") as mock_load,
    ):
        mock_load.return_value.llm.last_used_model = "gpt-4o"
        app = ModelPickerApp()
        app.force_selection = True
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.screen.query_one(DataTable)
            assert table.cursor_row == table.get_row_index("gpt-4o")


@pytest.mark.asyncio
async def test_model_selection_warns_instead_of_crashing_on_unwritable_state_config(mock_models):
    """PermissionError on state.save_config notifies and keeps selection for the session."""
    model_map = {m.name: m for m in mock_models}
    state = AppState(config=AppConfig(), config_file="/readonly/coursewright/config.yaml")

    with (
        patch("coursewright.ui.tui.screens.model_picker.get_models", return_value=mock_models),
        patch(
            "coursewright.ui.tui.screens.model_picker.get_model",
            side_effect=lambda name: model_map.get(name),
        ),
        patch.object(state, "save_config", side_effect=PermissionError("Permission denied")),
    ):
        app = ModelPickerApp(app_state=state)
        app.force_selection = True
        with patch.object(app, "notify") as mock_notify:
            async with app.run_test() as pilot:
                await pilot.pause()
                table = app.screen.query_one(DataTable)
                table.move_cursor(row=table.get_row_index("claude-3-opus"))
                await pilot.press("enter")
                await pilot.pause()

    assert app.selected_model == "claude-3-opus"
    assert state.config.llm.default_model == "claude-3-opus"
    assert state.config.llm.default_provider == "anthropic"
    warning_calls = [
        call
        for call in mock_notify.call_args_list
        if call.kwargs.get("severity") == "warning"
        and call.kwargs.get("title") == "Config Not Saved"
    ]
    assert len(warning_calls) == 1
    assert "/readonly/coursewright/config.yaml" in warning_calls[0].args[0]


@pytest.mark.asyncio
async def test_model_selection_warns_instead_of_crashing_on_unwritable_fallback_config(
    mock_models,
):
    """PermissionError on fallback config.save_to_file notifies and still dismisses."""
    model_map = {m.name: m for m in mock_models}
    mock_config = MagicMock(spec=AppConfig)
    mock_config.llm = MagicMock()
    mock_config.llm.default_model = "gpt-4o"
    mock_config.llm.last_used_model = None
    mock_config.save_to_file.side_effect = PermissionError("Permission denied")

    with (
        patch("coursewright.ui.tui.screens.model_picker.get_models", return_value=mock_models),
        patch(
            "coursewright.ui.tui.screens.model_picker.get_model",
            side_effect=lambda name: model_map.get(name),
        ),
        patch(
            "coursewright.ui.tui.screens.model_picker.AppConfig.load_from_file",
            return_value=mock_config,
        ),
    ):
        app = ModelPickerApp()
        with patch.object(app, "notify") as mock_notify:
            async with app.run_test() as pilot:
                await pilot.pause()
                table = app.screen.query_one(DataTable)
                table.move_cursor(row=table.get_row_index("claude-3-opus"))
                await pilot.press("enter")
                await pilot.pause()

    assert app.selected_model == "claude-3-opus"
    mock_config.llm.remember_selection.assert_called_with("anthropic", "claude-3-opus")
    assert mock_config.llm.default_model == "claude-3-opus"
    assert mock_config.llm.default_provider == "anthropic"
    warning_calls = [
        call
        for call in mock_notify.call_args_list
        if call.kwargs.get("severity") == "warning"
        and call.kwargs.get("title") == "Config Not Saved"
    ]
    assert len(warning_calls) == 1
    assert "config.yaml" in warning_calls[0].args[0]
