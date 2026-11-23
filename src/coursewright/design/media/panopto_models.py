"""Typed Pydantic v2 models for the Panopto client and media workflows.

Follows ``docs/HOUSE_STYLE.md`` section 1: public methods return Pydantic
v2 models rather than raw dictionaries, while ``DictCompatibleModel``
preserves ``dict`` access (``model["Id"]``, ``model.get("Name")``,
``{**record, ...}``) so existing dict-oriented callers keep working.
"""

from __future__ import annotations

from collections.abc import ItemsView, KeysView, ValuesView
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DictCompatibleModel(BaseModel):
    """Pydantic v2 base model that also supports mapping-style access.

    Callers may read fields as typed attributes (``session.name``,
    ``session.urls.caption_download_url``) or through dictionary methods
    (``session["Name"]``, ``session.get("Urls", {})``, ``{**record}``).
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @classmethod
    def _alias_to_field_map(cls) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for field_name, field_info in cls.model_fields.items():
            mapping[field_name] = field_name
            if field_info.alias:
                mapping[field_info.alias] = field_name
        return mapping

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dictionary representation (using aliases when defined)."""
        data = self.model_dump(by_alias=True)
        if self.model_extra:
            data.update(self.model_extra)
        return data

    def _mapping_view(self) -> dict[str, Any]:
        """Build a dictionary whose nested models remain dict-compatible."""
        result: dict[str, Any] = {}
        for field_name, field_info in self.__class__.model_fields.items():
            key = field_info.alias or field_name
            result[key] = getattr(self, field_name)
        if self.model_extra:
            result.update(self.model_extra)
        return result

    def __getitem__(self, key: str) -> Any:
        alias_map = self._alias_to_field_map()
        if key in alias_map:
            return getattr(self, alias_map[key])
        if self.model_extra and key in self.model_extra:
            return self.model_extra[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for ``key`` if present and not None, else ``default``."""
        try:
            value = self[key]
        except KeyError:
            return default
        return default if value is None and default is not None else value

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        alias_map = self._alias_to_field_map()
        return key in alias_map or bool(self.model_extra and key in self.model_extra)

    def keys(self) -> KeysView[str]:
        """Return mapping keys for ``dict(model)`` and ``{**model}`` unpacking."""
        return self._mapping_view().keys()

    def values(self) -> ValuesView[Any]:
        """Return mapping values."""
        return self._mapping_view().values()

    def items(self) -> ItemsView[str, Any]:
        """Return mapping ``(key, value)`` pairs."""
        return self._mapping_view().items()


class PanoptoSessionUrls(DictCompatibleModel):
    """URLs associated with a Panopto recording session."""

    viewer_url: str | None = Field(default=None, alias="ViewerUrl")
    embed_url: str | None = Field(default=None, alias="EmbedUrl")
    caption_download_url: str | None = Field(default=None, alias="CaptionDownloadUrl")
    thumbnail_url: str | None = Field(default=None, alias="ThumbnailUrl")


class PanoptoFolderDetails(DictCompatibleModel):
    """Folder metadata embedded inside a Panopto session payload."""

    id: str | None = Field(default=None, alias="Id")
    name: str | None = Field(default=None, alias="Name")


class PanoptoSession(DictCompatibleModel):
    """Typed Panopto session metadata returned by the Public REST API."""

    id: str | None = Field(default=None, alias="Id")
    name: str | None = Field(default=None, alias="Name")
    description: str | None = Field(default=None, alias="Description")
    duration: float | None = Field(default=None, alias="Duration")
    start_time: str | None = Field(default=None, alias="StartTime")
    folder_name: str | None = Field(default=None, alias="FolderName")
    urls: PanoptoSessionUrls = Field(default_factory=PanoptoSessionUrls, alias="Urls")
    folder_details: PanoptoFolderDetails = Field(
        default_factory=PanoptoFolderDetails, alias="FolderDetails"
    )

    @classmethod
    def from_api_payload(cls, payload: dict[str, Any] | PanoptoSession) -> PanoptoSession:
        """Normalize a raw Panopto API dictionary into a ``PanoptoSession``."""
        if isinstance(payload, cls):
            return payload
        normalized = dict(payload)
        if normalized.get("Urls") is None:
            normalized["Urls"] = {}
        if normalized.get("FolderDetails") is None:
            normalized["FolderDetails"] = {}
        return cls.model_validate(normalized)


class PanoptoVideoRecord(DictCompatibleModel):
    """A discovered Panopto video reference from a Canvas page or folder scan."""

    url: str
    source_type: str = "page"
    title: str = ""
    unit_name: str = ""


class PanoptoCaptionRecord(DictCompatibleModel):
    """Enriched Panopto session record produced by the caption download workflow."""

    url: str
    source_type: str = "page"
    title: str = ""
    unit_name: str = ""
    session_id: str | None = None
    session_name: str | None = None
    viewer_url: str | None = None
    caption_download_url: str | None = None
    caption_file: str | None = None
    status: str = "no-captions"
    error: str | None = None


class PanoptoOAuthDebugInfo(DictCompatibleModel):
    """Redacted OAuth2 PKCE diagnostic settings (never contains secrets)."""

    credential_source: str
    authorization_url: str
    parameters: dict[str, str]
