"""Tests for core/config settings, date format, workspace resolution, and layering."""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from coursewright.core import config as core_config
from coursewright.core.config import (
    CANVAS_DATE_FORMAT,
    DATE_FORMAT_INPUT,
    DATE_FORMAT_OUTPUT,
    DEFAULT_CANVAS_URL,
    DEFAULT_INTRO_MODULES,
    SCHEDULE_DATE_FORMAT,
    WORKSPACE_ENVIRONMENT_VARIABLE,
    AppConfig,
    AssistantConfig,
    CLIConfig,
    CourseDesignConfig,
    DiscussionConfig,
    ExportConfig,
    ExportFormat,
    LLMConfig,
    TemplateConfig,
    find_workspace_directory,
    format_canvas_datetime,
    resolve_workspace_directory,
)
from coursewright.ui.cli.main import app


class TestExportConfig:
    """Ported from discussorator ``tests/test_models.py``."""

    def test_output_directory_created(self, tmp_path: Path) -> None:
        test_dir = tmp_path / "test_exports"
        assert not test_dir.exists()

        ExportConfig(output_directory=str(test_dir))

        assert test_dir.exists()


class TestCanvasDateFormat:
    """Verify Canvas date formatting rules (AGENTS.md 3.10)."""

    def test_canvas_date_format_constant_has_no_z_suffix(self) -> None:
        assert CANVAS_DATE_FORMAT == "%Y-%m-%dT%H:%M:%S"
        assert DATE_FORMAT_OUTPUT == CANVAS_DATE_FORMAT
        assert not CANVAS_DATE_FORMAT.endswith("Z")
        assert SCHEDULE_DATE_FORMAT == DATE_FORMAT_INPUT == "%m/%d/%Y %I:%M %p"

    def test_format_canvas_datetime_produces_iso_without_z(self) -> None:
        moment = datetime(2026, 9, 14, 23, 59, 0)
        rendered = format_canvas_datetime(moment)
        assert rendered == "2026-09-14T23:59:00"
        assert not rendered.endswith("Z")

    def test_design_config_rejects_date_format_ending_with_z(self) -> None:
        with pytest.raises(ValidationError, match="must not end with 'Z'"):
            CourseDesignConfig(date_format_output="%Y-%m-%dT%H:%M:%SZ")


class TestUnifiedSettingsModel:
    """Verify the unified Pydantic v2 settings model across both halves."""

    def test_default_app_config_uses_neutral_defaults(self) -> None:
        cfg = AppConfig()
        assert cfg.discussion.canvas_url == DEFAULT_CANVAS_URL
        assert cfg.design.canvas_url == DEFAULT_CANVAS_URL
        assert cfg.discussion.canvas_url == "https://canvas.example.edu"
        assert cfg.design.intro_modules == list(DEFAULT_INTRO_MODULES)
        assert cfg.design.intro_modules == [
            "Getting Started",
            "Course Resources",
            "Assignments",
            "Syllabus",
        ]
        assert cfg.templates.course_policy == "course_policy.md"
        assert cfg.workspace_directory is None

    def test_outreach_contact_defaults_are_placeholders_and_load_from_dict(self) -> None:
        cfg = AppConfig()
        assert cfg.outreach.instructor_email.endswith("@example.edu")
        assert cfg.outreach.institution_name == "Your institution"
        assert cfg.outreach.lab_example_url == ""

        loaded = AppConfig.model_validate(
            {"outreach": {"instructor_signature": "Dr. Example", "lab_example_url": "x"}}
        )
        assert loaded.outreach.instructor_signature == "Dr. Example"
        assert loaded.outreach.lab_example_url == "x"
        assert loaded.outreach.office_hours_url == "https://example.edu/office-hours"

    def test_llm_config_selection_and_api_key_resolution(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        llm = LLMConfig(provider="openai", model="gpt-4o-mini")
        assert llm.provider == "openai"
        assert llm.model == "gpt-4o-mini"
        assert llm.get_api_key() == ""

        llm.remember_selection("anthropic", "claude-3-5-haiku-20241022")
        assert llm.provider == "anthropic"
        assert llm.model == "claude-3-5-haiku-20241022"

        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-secret-key")
        assert llm.get_api_key() == "env-secret-key"

        key_file = tmp_path / "llm.key"
        key_file.write_text("file-secret-key\n", encoding="utf-8")
        llm.api_key_file = str(key_file)
        assert llm.get_api_key() == "file-secret-key"

        llm.api_key = "direct-secret-key"
        assert llm.get_api_key() == "direct-secret-key"

    def test_assistant_and_cli_config_defaults(self) -> None:
        assistant = AssistantConfig()
        assert not assistant.enabled
        assert assistant.ranking.depth_weight == 0.4
        assert assistant.plagiarism_threshold == 0.85

        cli = CLIConfig()
        assert cli.default_course_id is None
        assert cli.show_progress_bars is True
        assert cli.recent_courses == []

    def test_design_config_validators(self) -> None:
        with pytest.raises(ValidationError, match="rate_limit_delay"):
            CourseDesignConfig(rate_limit_delay=-0.1)
        with pytest.raises(ValidationError, match="per_page"):
            CourseDesignConfig(per_page=0)
        with pytest.raises(ValidationError, match="per_page"):
            CourseDesignConfig(per_page=101)
        with pytest.raises(ValidationError, match="canvas_url"):
            CourseDesignConfig(canvas_url="ftp://canvas.example.edu")

    def test_template_config_resolves_workspace_first(self, tmp_path: Path) -> None:
        templates = TemplateConfig(custom={"welcome": "welcome_note.md"})
        assert templates.resolve_filename("course_policy") == "course_policy.md"
        assert templates.resolve_filename("welcome") == "welcome_note.md"
        assert templates.resolve_filename("literal.md") == "literal.md"

        default_path = templates.resolve_path("course_policy", tmp_path)
        assert default_path == Path("templates") / "course_policy.md"

        ws_templates = tmp_path / "templates"
        ws_templates.mkdir()
        ws_policy = ws_templates / "course_policy.md"
        ws_policy.write_text("Workspace policy", encoding="utf-8")
        assert templates.resolve_path("course_policy", tmp_path) == ws_policy

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        original = AppConfig(
            workspace=str(tmp_path / "ws"),
            discussion=DiscussionConfig(
                canvas_url="https://school.example.edu",
                default_min_words=250,
                export=ExportConfig(
                    format=ExportFormat.EXCEL,
                    output_directory=str(tmp_path / "exports"),
                ),
            ),
        )
        original.save_to_file(config_path)

        loaded = AppConfig.load_from_file(config_path)
        assert loaded.workspace_directory == str(tmp_path / "ws")
        assert loaded.discussion.canvas_url == "https://school.example.edu"
        assert loaded.discussion.default_min_words == 250
        assert loaded.discussion.export.format == ExportFormat.EXCEL

    def test_load_from_file_creates_default_when_missing(self, tmp_path: Path) -> None:
        missing_path = tmp_path / "created_config.yaml"
        loaded = AppConfig.load_from_file(missing_path)
        assert missing_path.is_file()
        assert loaded.discussion.canvas_url == DEFAULT_CANVAS_URL


class TestWorkspaceResolution:
    """Verify private workspace resolution order: env first, then config."""

    def test_env_variable_wins_over_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_ws = tmp_path / "env_workspace"
        cfg_ws = tmp_path / "cfg_workspace"
        monkeypatch.setenv(WORKSPACE_ENVIRONMENT_VARIABLE, str(env_ws))
        cfg = AppConfig(workspace=str(cfg_ws))

        assert resolve_workspace_directory(cfg) == env_ws
        assert find_workspace_directory(cfg) == env_ws

    def test_falls_back_to_config_object_then_config_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(WORKSPACE_ENVIRONMENT_VARIABLE, raising=False)
        cfg_ws = tmp_path / "from_object"
        cfg = AppConfig(workspace=str(cfg_ws))
        assert resolve_workspace_directory(cfg) == cfg_ws

        config_file = tmp_path / "config.yaml"
        file_ws = tmp_path / "from_file"
        AppConfig(workspace=str(file_ws)).save_to_file(config_file)
        assert resolve_workspace_directory(config_file=config_file) == file_ws

        monkeypatch.chdir(tmp_path)
        assert find_workspace_directory() == file_ws

    def test_returns_none_without_creating_files_when_unconfigured(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(WORKSPACE_ENVIRONMENT_VARIABLE, raising=False)
        monkeypatch.chdir(tmp_path)
        assert find_workspace_directory() is None
        assert not (tmp_path / "config.yaml").exists()

    def test_cli_design_rubrics_honours_env_and_config_workspace_overrides(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("COLUMNS", "200")
        workspace = tmp_path / "private_ws"
        rubrics_dir = workspace / "rubrics"
        rubrics_dir.mkdir(parents=True)
        (rubrics_dir / "custom-local.toml").write_text(
            """
[rubric]
id = "custom-local"
name = "Workspace Custom Rubric"
source_url = "https://example.org/custom"
text_included = true

[[section]]
id = "s1"
name = "Section 1"

[[standard]]
id = "1"
section = "s1"
text = "Standard one."
""",
            encoding="utf-8",
        )

        runner = CliRunner()
        monkeypatch.setenv(WORKSPACE_ENVIRONMENT_VARIABLE, str(workspace))
        env_result = runner.invoke(app, ["design", "rubrics"])
        assert env_result.exit_code == 0
        assert "custom-local" in env_result.output

        monkeypatch.delenv(WORKSPACE_ENVIRONMENT_VARIABLE, raising=False)
        monkeypatch.chdir(tmp_path)
        AppConfig(workspace=str(workspace)).save_to_file(tmp_path / "config.yaml")
        cfg_result = runner.invoke(app, ["design", "rubrics"])
        assert cfg_result.exit_code == 0
        assert "custom-local" in cfg_result.output


def test_config_package_imports_no_user_interface_library() -> None:
    """Acceptance 2: no module under core/config imports rich, typer, textual, or ui."""
    forbidden_roots = {"rich", "typer", "textual"}
    violations: list[str] = []
    for source_path in Path(core_config.__file__).parent.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported_names = [node.module or ""]
            else:
                continue
            for imported_name in imported_names:
                if (
                    imported_name.split(".")[0] in forbidden_roots
                    or imported_name.startswith("coursewright.ui")
                ):
                    violations.append(f"{source_path.name}: {imported_name}")
    assert not violations, "\n".join(violations)
