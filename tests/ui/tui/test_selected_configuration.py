"""A TUI session keeps reads and writes tied to its selected config."""

import pytest

from coursewright.core.canvas.safe_write import SafeWriteError, create_safe_canvas_writer
from coursewright.core.config import AppConfig, resolve_workspace_directory
from coursewright.ui.tui.app import CoursewrightApp
from coursewright.ui.tui.screens.attendance import _outreach_contact_from_config
from coursewright.ui.tui.state import AppState


def test_app_preserves_selected_config_and_saves_only_selected_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    default_path = tmp_path / "config.yaml"
    default_path.write_text("workspace: unrelated\n")
    selected_path = tmp_path / "selected.yaml"
    selected = AppConfig(workspace=str(tmp_path / "selected-workspace"))
    selected.outreach.instructor_signature = "Selected instructor"
    app = CoursewrightApp(config=selected, config_file=str(selected_path))

    state = app.app_state
    state.config.discussion.default_grading_profile = "legacy"
    state.save_config()

    assert state.config is selected
    assert state.workspace_directory == tmp_path / "selected-workspace"
    assert _outreach_contact_from_config(state.config).instructor_signature == "Selected instructor"
    assert AppConfig.load_from_file(selected_path).discussion.default_grading_profile == "legacy"
    assert default_path.read_text() == "workspace: unrelated\n"


def test_selected_config_without_workspace_refuses_unrelated_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    (tmp_path / "config.yaml").write_text("workspace: unrelated\n")
    state = AppState(config=AppConfig())

    with pytest.raises(SafeWriteError):
        create_safe_canvas_writer(object(), workspace_directory=state.workspace_directory)
    with pytest.raises(SafeWriteError):
        _ = state.backup_directory


def test_workspace_lookup_propagates_invalid_config(tmp_path, monkeypatch):
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    config_file = tmp_path / "invalid.yaml"
    config_file.write_text("[]")

    with pytest.raises(ValueError, match="root must be a mapping"):
        resolve_workspace_directory(config_file=config_file)
