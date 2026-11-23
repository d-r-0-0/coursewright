"""Configuration management logic and private workspace resolution.

Ported from discussorator ``cli/config_manager.py`` (pure logic without
Rich/Typer prompts or printing) and COURSE_BUILDER ``config.py``
(``validate_config``, ``get_config_summary``).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from coursewright.core.config.settings import (
    DEFAULT_CONFIG_FILENAME,
    WORKSPACE_ENVIRONMENT_VARIABLE,
    AppConfig,
)


class ConfigError(ValueError):
    """Raised when reading, mutating, or validating configuration fails."""


@dataclass(frozen=True)
class ConfigValidationResult:
    """Errors and warnings discovered when validating an ``AppConfig``."""

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return True when validation found no errors."""
        return not self.errors


def resolve_workspace_directory(
    config: AppConfig | None = None,
    config_file: str | Path | None = None,
) -> Path | None:
    """Return the instructor's private workspace directory, if configured.

    Resolution order (card SCOPE):
      1. ``COURSEWRIGHT_WORKSPACE`` environment variable.
      2. ``config.workspace_directory`` (from the passed ``AppConfig`` or
         from ``config_file`` if it exists on disk).
    """
    env_value = os.environ.get(WORKSPACE_ENVIRONMENT_VARIABLE, "").strip()
    if env_value:
        return Path(env_value).expanduser()

    resolved_config = config
    if resolved_config is None:
        candidate_path = Path(config_file or DEFAULT_CONFIG_FILENAME)
        if candidate_path.is_file():
            resolved_config = AppConfig.load_from_file(
                candidate_path,
                create_if_missing=False,
            )

    if resolved_config is not None and resolved_config.workspace_directory:
        configured = str(resolved_config.workspace_directory).strip()
        if configured:
            return Path(configured).expanduser()

    return None


find_workspace_directory = resolve_workspace_directory


def get_config_value(config: AppConfig, setting_path: str) -> Any:
    """Retrieve a configuration value using dotted notation."""
    parts = [part for part in setting_path.split(".") if part]
    if not parts:
        raise ConfigError("Setting path cannot be empty")

    current: Any = config
    for index, part in enumerate(parts):
        if not hasattr(current, part):
            prefix = ".".join(parts[:index]) or "root"
            raise ConfigError(f"Setting '{part}' not found in {prefix}")
        current = getattr(current, part)
    return current


def _coerce_setting_value(target_parent: Any, field_name: str, raw_value: str) -> Any:
    """Convert ``raw_value`` to the target field's expected Python type."""
    current_value = getattr(target_parent, field_name)
    value_type = type(current_value)
    annotation_text = ""
    if isinstance(target_parent, BaseModel):
        field_info = target_parent.__class__.model_fields.get(field_name)
        annotation_text = str(field_info.annotation) if field_info else ""

    if "None" in annotation_text and raw_value.strip().lower() in {"none", "null", ""}:
        return None

    try:
        if isinstance(current_value, Enum):
            enum_class = type(current_value)
            return enum_class(raw_value)
        if value_type is bool:
            lowered = raw_value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"cannot parse boolean from {raw_value!r}")
        if value_type is int or (current_value is None and "int" in annotation_text):
            return int(raw_value)
        if value_type is float or (current_value is None and "float" in annotation_text):
            return float(raw_value)
        return raw_value
    except (ValueError, TypeError) as err:
        raise ConfigError(
            f"Invalid value '{raw_value}' for setting '{field_name}' "
            f"(expected {value_type.__name__})"
        ) from err


def set_config_value(
    config: AppConfig,
    setting_path: str,
    raw_value: str,
    config_file: str | Path | None = None,
) -> tuple[Any, Any]:
    """Update a dotted setting on ``config`` and optionally save to disk.

    Returns:
        ``(old_value, new_value)`` tuple.
    """
    parts = [part for part in setting_path.split(".") if part]
    if not parts:
        raise ConfigError("Setting path cannot be empty")

    current: Any = config
    for part in parts[:-1]:
        if not hasattr(current, part):
            raise ConfigError(f"Invalid setting path: {setting_path}")
        current = getattr(current, part)

    final_key = parts[-1]
    if not hasattr(current, final_key):
        parent_label = ".".join(parts[:-1]) or "root"
        raise ConfigError(f"Setting '{final_key}' not found in {parent_label}")

    old_value = getattr(current, final_key)
    converted_value = _coerce_setting_value(current, final_key, raw_value)
    setattr(current, final_key, converted_value)

    if config_file is not None:
        try:
            config.save_to_file(config_file)
        except OSError as err:
            raise ConfigError(f"Could not save configuration to {config_file}: {err}") from err

    return old_value, converted_value


def reset_config_file(
    config_file: str | Path = DEFAULT_CONFIG_FILENAME,
) -> tuple[AppConfig, Path | None]:
    """Reset ``config_file`` to defaults, backing up any existing file first.

    Returns:
        ``(default_config, backup_path)`` where ``backup_path`` is None if
        ``config_file`` did not exist before the reset.
    """
    config_path = Path(config_file)
    backup_path: Path | None = None

    try:
        if config_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = config_path.with_suffix(f".backup_{timestamp}.yaml")
            shutil.copy2(config_path, backup_path)

        default_config = AppConfig()
        default_config.save_to_file(config_path)
    except OSError as err:
        raise ConfigError(f"Error resetting configuration at {config_path}: {err}") from err

    return default_config, backup_path


def export_config_file(config: AppConfig, output_file: str | Path) -> Path:
    """Export ``config`` to ``output_file`` and return its path."""
    output_path = Path(output_file)
    try:
        config.save_to_file(output_path)
    except OSError as err:
        raise ConfigError(f"Error exporting configuration to {output_path}: {err}") from err
    return output_path


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def import_config_file(
    input_file: str | Path,
    config_file: str | Path = DEFAULT_CONFIG_FILENAME,
    *,
    merge: bool = False,
) -> AppConfig:
    """Import configuration from ``input_file`` and save it to ``config_file``."""
    input_path = Path(input_file)
    if not input_path.exists():
        raise ConfigError(f"Configuration file not found: {input_file}")

    config_path = Path(config_file)
    try:
        if merge and config_path.exists():
            existing_raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            incoming_raw = yaml.safe_load(input_path.read_text(encoding="utf-8")) or {}
            if not isinstance(existing_raw, dict) or not isinstance(incoming_raw, dict):
                raise ConfigError("Merged configuration files must contain YAML mappings")
            merged_data = _deep_merge_dicts(existing_raw, incoming_raw)
            new_config = AppConfig(**merged_data)
        else:
            new_config = AppConfig.load_from_file(input_path, create_if_missing=False)

        new_config.save_to_file(config_path)
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as err:
        if isinstance(err, ConfigError):
            raise
        raise ConfigError(f"Error importing configuration from {input_file}: {err}") from err

    return new_config


def validate_config(config: AppConfig | None = None) -> ConfigValidationResult:
    """Check ``config`` for errors, missing files, and invalid settings."""
    active_config = config or AppConfig()
    errors: list[str] = []
    warnings: list[str] = []

    if not active_config.discussion.canvas_url:
        errors.append("Canvas URL is not set")
    elif not active_config.discussion.canvas_url.startswith(("http://", "https://")):
        errors.append("Canvas URL must start with http:// or https://")

    token_file = Path(active_config.discussion.api_token_file)
    if not token_file.exists():
        warnings.append(f"API token file not found: {token_file}")
    elif not token_file.read_text(encoding="utf-8").strip():
        warnings.append("API token file is empty")

    if active_config.discussion.default_min_words < 0:
        errors.append("Default minimum words cannot be negative")
    if active_config.discussion.default_min_replies < 0:
        errors.append("Default minimum replies cannot be negative")

    export_dir = Path(active_config.discussion.export.output_directory)
    try:
        export_dir.mkdir(exist_ok=True, parents=True)
    except OSError:
        errors.append(f"Cannot create export directory: {export_dir}")

    if active_config.discussion.api_delay < 0:
        errors.append("API delay cannot be negative")
    if active_config.discussion.max_retries < 0:
        errors.append("Max retries cannot be negative")

    if active_config.design.rate_limit_delay < 0:
        errors.append("Design rate_limit_delay must be >= 0")
    if active_config.design.per_page < 1 or active_config.design.per_page > 100:
        errors.append("Design per_page must be between 1 and 100")
    if active_config.design.date_format_output.endswith("Z"):
        errors.append("Canvas date_format_output must not end with 'Z'")

    return ConfigValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def get_config_summary(config: AppConfig | None = None) -> dict[str, Any]:
    """Return a concise summary dictionary of the active configuration."""
    active_config = config or AppConfig()
    return {
        "canvas_url": active_config.discussion.canvas_url,
        "api_key_path": active_config.discussion.api_token_file,
        "rate_limit_delay": active_config.design.rate_limit_delay,
        "per_page": active_config.design.per_page,
        "discussion_points": active_config.design.discussion_points,
        "unit_count": active_config.design.unit_count,
        "dry_run_default": active_config.design.dry_run_default,
        "verbose": active_config.design.verbose,
        "workspace_directory": active_config.workspace_directory,
    }
