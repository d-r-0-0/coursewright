"""Tests for core/config manager operations and logger utilities."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from coursewright.core.config import (
    AppConfig,
    ConfigError,
    DiscussionConfig,
    DryRunContext,
    ExportConfig,
    ExportFormat,
    export_config_file,
    get_config_summary,
    get_config_value,
    import_config_file,
    log_action,
    log_error,
    log_progress,
    log_section,
    log_success,
    log_warning,
    reset_config_file,
    set_config_value,
    setup_logger,
    validate_config,
)


class TestConfigManagerLogic:
    """Verify pure config management functions ported from config_manager.py."""

    def test_get_and_set_config_values(self, tmp_path: Path) -> None:
        cfg = AppConfig()
        config_file = tmp_path / "config.yaml"

        old, new = set_config_value(
            cfg,
            "discussion.default_min_words",
            "200",
            config_file=config_file,
        )
        assert (old, new) == (300, 200)
        assert get_config_value(cfg, "discussion.default_min_words") == 200

        set_config_value(cfg, "discussion.include_ta_posts", "true")
        assert cfg.discussion.include_ta_posts is True

        set_config_value(cfg, "discussion.include_ta_posts", "false")
        assert cfg.discussion.include_ta_posts is False

        set_config_value(cfg, "discussion.api_delay", "0.25")
        assert cfg.discussion.api_delay == 0.25

        set_config_value(cfg, "discussion.export.format", "xlsx")
        assert cfg.discussion.export.format == ExportFormat.EXCEL

        set_config_value(cfg, "cli.default_course_id", "12345")
        assert cfg.cli.default_course_id == 12345

        set_config_value(cfg, "cli.default_course_id", "none")
        assert cfg.cli.default_course_id is None

    def test_set_and_get_reject_invalid_paths_and_values(self) -> None:
        cfg = AppConfig()
        with pytest.raises(ConfigError, match="cannot be empty"):
            get_config_value(cfg, "")
        with pytest.raises(ConfigError, match="not found"):
            get_config_value(cfg, "discussion.no_such_field")
        with pytest.raises(ConfigError, match="Invalid setting path"):
            set_config_value(cfg, "nonexistent.field", "1")
        with pytest.raises(ConfigError, match="not found"):
            set_config_value(cfg, "discussion.missing", "1")
        with pytest.raises(ConfigError, match="Invalid value"):
            set_config_value(cfg, "discussion.default_min_words", "not-an-int")
        with pytest.raises(ConfigError, match="Invalid value"):
            set_config_value(cfg, "discussion.include_ta_posts", "maybe")

    def test_reset_export_and_import_config_files(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        custom = AppConfig()
        custom.discussion.default_min_words = 450
        custom.save_to_file(config_file)

        reset_cfg, backup_path = reset_config_file(config_file)
        assert reset_cfg.discussion.default_min_words == 300
        assert backup_path is not None and backup_path.is_file()

        exported_path = tmp_path / "exported.yaml"
        export_config_file(custom, exported_path)
        assert exported_path.is_file()

        imported = import_config_file(exported_path, config_file)
        assert imported.discussion.default_min_words == 450

        override_file = tmp_path / "override.yaml"
        override_file.write_text(
            "discussion:\n  default_min_replies: 4\n",
            encoding="utf-8",
        )
        merged = import_config_file(override_file, config_file, merge=True)
        assert merged.discussion.default_min_words == 450
        assert merged.discussion.default_min_replies == 4

        with pytest.raises(ConfigError, match="not found"):
            import_config_file(tmp_path / "absent.yaml", config_file)

    def test_validate_config_and_summary(self, tmp_path: Path) -> None:
        token_file = tmp_path / "api.key"
        token_file.write_text("synthetic-token\n", encoding="utf-8")
        cfg = AppConfig(
            discussion=DiscussionConfig(
                api_token_file=str(token_file),
                export=ExportConfig(output_directory=str(tmp_path / "exports")),
            ),
        )
        valid_result = validate_config(cfg)
        assert valid_result.is_valid
        assert valid_result.errors == ()
        assert valid_result.warnings == ()

        cfg.discussion.canvas_url = "invalid-url"
        cfg.discussion.default_min_words = -5
        invalid_result = validate_config(cfg)
        assert not invalid_result.is_valid
        assert any("Canvas URL" in err for err in invalid_result.errors)
        assert any("minimum words" in err for err in invalid_result.errors)

        summary = get_config_summary(cfg)
        assert summary["unit_count"] == 15
        assert summary["per_page"] == 100


class TestLoggerUtilities:
    """Verify stdlib logger utilities ported from COURSE_BUILDER."""

    def test_logger_writes_to_file_and_stays_silent_by_default(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        log_path = tmp_path / "run.log"
        logger = setup_logger("test_coursewright", level=logging.DEBUG, log_file=log_path)

        with DryRunContext(logger, dry_run=True):
            log_action(logger, "Update module", dry_run=True)
            log_progress(logger, 1, 2, "units")
            log_success(logger, "Checked unit")
            log_warning(logger, "Minor issue")
            log_error(logger, "Sample error")
            log_section(logger, "Summary")

        captured = capsys.readouterr()
        assert captured.out == ""
        content = log_path.read_text(encoding="utf-8")
        assert "DRY RUN MODE" in content
        assert "Would Update module" in content
        assert "Progress: 1/2 units (50.0%)" in content

    def test_live_mode_context_logs_completion(self, tmp_path: Path) -> None:
        log_path = tmp_path / "live.log"
        logger = setup_logger("test_live", log_file=log_path)
        with DryRunContext(logger, dry_run=False):
            log_action(logger, "Apply dates", dry_run=False)
        content = log_path.read_text(encoding="utf-8")
        assert "LIVE MODE" in content
        assert "Executing: Apply dates" in content
        assert "Changes complete" in content
