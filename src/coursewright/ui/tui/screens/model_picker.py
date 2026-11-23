"""LLM Model Selection Screen.

A screen for selecting the LLM model to use for grading and analysis.
Displays available models from the registry in a sortable DataTable,
grouped by provider. Selection persists to the local configuration.

If a last-used model is saved in the config, the picker auto-selects it
and dismisses immediately — unless ``force_selection=True`` is passed
(or the user presses the "c" keybinding to re-open the full list).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static

from coursewright.core.config.settings import AppConfig
from coursewright.core.llm.model_registry import (
    get_model,
    get_models,
    get_pricing_staleness_warning,
)
from coursewright.ui.tui.screens.base import CoursewrightScreen


class ModelPickerScreen(CoursewrightScreen):
    """Screen for selecting an LLM model from the available options."""

    BINDINGS = [
        Binding("c", "change_model", "Change model", show=True),
    ]

    CSS = """
    ModelPickerScreen {
        align: center middle;
    }

    DataTable {
        width: 100%;
        height: 100%;
        border: solid green;
    }

    #pricing-staleness-banner {
        color: ansi_bright_yellow;
        background: $surface;
        text-style: dim bold;
        padding: 0 1;
        margin: 0 1;
    }
    """

    def __init__(self, force_selection: bool = False) -> None:
        super().__init__()
        self.force_selection = force_selection
        self.preferred_model_name: str | None = None

    def compose(self) -> ComposeResult:
        """Create the child widgets for the screen."""
        yield Header()
        staleness_warning = get_pricing_staleness_warning()
        if staleness_warning:
            yield Static(f"⚠ {staleness_warning}", id="pricing-staleness-banner")
        yield DataTable(cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        """Populate the table with available models on startup.

        When a last-used model is saved and ``force_selection`` is False,
        the screen auto-dismisses with that model — no user interaction
        required.  This makes the common "keep using the same model"
        path instant.
        """
        state = getattr(self.app, "app_state", None)
        config = state.config if state is not None else AppConfig.load_from_file()
        configured_last_used_model = getattr(config.llm, "last_used_model", None)
        # Only honour the saved model if it still exists in the registry.
        # Stale names (e.g. 'gpt-3.5-turbo') would crash move_cursor later.
        available_model_names = {m.name for m in get_models()}
        self.preferred_model_name = (
            configured_last_used_model
            if isinstance(configured_last_used_model, str)
            and configured_last_used_model.strip()
            and configured_last_used_model in available_model_names
            else None
        )

        # Fast-path: re-use the saved model without showing the picker
        if self.preferred_model_name and not self.force_selection:
            self.app.notify(
                f"Using saved model: {self.preferred_model_name}",
                title="Model Selection",
            )
            self.dismiss(self.preferred_model_name)
            return

        table = self.query_one(DataTable)
        table.add_columns(
            "Provider",
            "Model",
            "Speed",
            "Input $/1k",
            "Description",
        )

        # Get models (they are already sorted by provider, then name)
        for model in get_models():
            table.add_row(
                model.provider.value,
                model.name,
                model.speed,
                f"${model.input_cost_per_1k:.4f}",
                model.description,
                key=model.name,
            )

        # Pre-select the last-used model row when forcing re-selection
        if self.preferred_model_name:
            table.move_cursor(row=table.get_row_index(self.preferred_model_name))

        table.focus()

    def action_change_model(self) -> None:
        """Force picker interaction even when a saved model exists."""
        if self.force_selection:
            self.app.notify("Model picker is already open.")
            return

        self.force_selection = True
        self.refresh(recompose=True)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle model selection."""
        model_name = event.row_key.value
        if not model_name:
            return

        # Load config to update it
        state = getattr(self.app, "app_state", None)
        config = state.config if state is not None else AppConfig.load_from_file()

        # Get metadata to identify the provider
        model_metadata = get_model(model_name)

        if model_metadata:
            provider = model_metadata.provider.value

            # Update configuration
            config.llm.remember_selection(provider, model_name)
            config.llm.default_model = model_name
            config.llm.default_provider = provider

            # Save configuration
            self._persist_config_or_warn(state, fallback_config=config)

            # Return the selected model name to the caller
            self.dismiss(model_name)
        else:
            self.app.notify(f"Error: Model {model_name} not found in registry", severity="error")
