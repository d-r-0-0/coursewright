"""Panopto caption discovery, session listing, and OAuth consumer workflows.

Ported from COURSE_BUILDER ``cantrips/fetch_panopto_captions.py``,
``cantrips/find_panopto_sessions.py``, and the OAuth/diagnostic consumer
flows. All functions are silent library routines (AGENTS.md 3.8): they
never print or prompt, use configured URLs and folder IDs rather than
institution-specific constants, and route all network traffic through
``PanoptoClient._make_request``.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import os
import re
import secrets
import urllib.parse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from coursewright.design.media.panopto_api import (
    DEFAULT_PANOPTO_BASE_URL,
    PanoptoClient,
    parse_panopto_session_id,
    sanitize_filename_component,
)
from coursewright.design.media.panopto_auth import (
    PanoptoClientCredentials,
    PanoptoPasswordGrantAuth,
    PanoptoTokenAuth,
    load_panopto_client_credentials,
)
from coursewright.design.media.panopto_models import (
    PanoptoCaptionRecord,
    PanoptoOAuthDebugInfo,
    PanoptoSession,
    PanoptoVideoRecord,
)

logger = logging.getLogger(__name__)

PANOPTO_REGEX = re.compile(
    r"(?:https?:)?//[^\"'\s>]*panopto[^\"'\s<)]*",
    re.IGNORECASE,
)

DEFAULT_OAUTH_REDIRECT_URI = "http://localhost:8765/callback"
DEFAULT_OAUTH_SCOPES = "openid api offline_access"
DIAGNOSTIC_ENDPOINTS: tuple[str, ...] = (
    "/api/v1/sessions?api-version=1",
    "/api/sessions",
    "/PublicAPI/REST/sessions",
    "/PublicAPI/REST/sessionSearch",
)


def normalize_panopto_url(url: str) -> str:
    """Normalize a Panopto URL so de-duplication works across embed styles."""
    candidate = html.unescape(url.strip().strip("\"'"))
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    parsed = urlparse(candidate)
    query = parse_qs(parsed.query)
    normalized_query = f"id={query['id'][0]}" if "id" in query and query["id"] else parsed.query
    return parsed._replace(query=normalized_query, fragment="").geturl()


def extract_panopto_urls(text: str) -> list[str]:
    """Extract unique normalized Panopto URLs from HTML or plain text."""
    if not text:
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for match in PANOPTO_REGEX.finditer(text):
        normalized = normalize_panopto_url(match.group(0))
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
    return urls


def get_page_body(canvas: Any, course_id: int, page_url: str) -> str:
    """Fetch a Canvas page's HTML body via the provided Canvas client."""
    url = f"{canvas.base_url}/api/v1/courses/{course_id}/pages/{page_url}"
    response = canvas._make_request("GET", url)
    if response and response.status_code == 200:
        return response.json().get("body", "") or ""
    return ""


def scan_canvas_course(canvas: Any, course_id: int) -> list[PanoptoVideoRecord]:
    """Return one record per unique Panopto URL found across all course pages."""
    modules = canvas.get_modules(course_id)
    pages = canvas.get_pages(course_id)

    unit_map: dict[str, str] = {}
    for module in modules:
        unit_name = module.get("name", "")
        for item in module.get("items", []):
            for key in (item.get("title", ""), item.get("page_url", "")):
                if key:
                    unit_map[key] = unit_name

    records: list[PanoptoVideoRecord] = []
    seen_urls: set[str] = set()

    for page in pages:
        page_url = page.get("url", "")
        title = page.get("title", "")
        body = get_page_body(canvas, course_id, page_url)
        unit_name = unit_map.get(page_url) or unit_map.get(title, "")

        for panopto_url in extract_panopto_urls(body):
            if panopto_url in seen_urls:
                continue
            seen_urls.add(panopto_url)
            records.append(
                PanoptoVideoRecord(
                    url=panopto_url,
                    source_type="page",
                    title=title,
                    unit_name=unit_name,
                )
            )

    records.sort(key=lambda r: (r.unit_name, r.title))
    return records


def scan_panopto_folder(
    panopto: PanoptoClient, folder_id: str | None = None
) -> list[PanoptoVideoRecord]:
    """Return one record per session in a Panopto folder."""
    records: list[PanoptoVideoRecord] = []
    for session in panopto.list_folder_sessions(folder_id):
        session_id = session.get("Id")
        if not session_id:
            continue
        folder_details = session.get("FolderDetails") or {}
        records.append(
            PanoptoVideoRecord(
                url=f"{panopto.base_url}/Pages/Viewer.aspx?id={session_id}",
                source_type="folder",
                title=session.get("Name", "") or "",
                unit_name=folder_details.get("Name", "") or "",
            )
        )
    records.sort(key=lambda r: r.title)
    return records


def detect_caption_extension(caption_url: str) -> str:
    """Infer a caption file extension (``.srt``, ``.vtt``, or ``.txt``)."""
    lowered = caption_url.lower()
    if ".srt" in lowered:
        return ".srt"
    if ".vtt" in lowered:
        return ".vtt"
    return ".txt"


def download_captions(
    panopto: PanoptoClient,
    records: Sequence[Mapping[str, Any] | PanoptoVideoRecord],
    captions_dir: Path,
) -> list[PanoptoCaptionRecord]:
    """Enrich each record with session metadata and download caption files."""
    captions_dir.mkdir(parents=True, exist_ok=True)
    seen_session_ids: set[str] = set()
    enriched: list[PanoptoCaptionRecord] = []

    for raw_record in records:
        record = (
            raw_record.to_dict() if isinstance(raw_record, PanoptoVideoRecord) else dict(raw_record)
        )
        session_id = parse_panopto_session_id(record.get("url", ""))
        if not session_id:
            enriched.append(
                PanoptoCaptionRecord(
                    **record,
                    session_id=None,
                    status="invalid-url",
                    caption_file=None,
                )
            )
            continue

        if session_id in seen_session_ids:
            continue
        seen_session_ids.add(session_id)

        try:
            session = panopto.get_session(session_id)
            session_name = session.get("Name") or record.get("title", "")
            urls = session.get("Urls") or {}
            caption_url = urls.get("CaptionDownloadUrl") or ""

            caption_file = None
            status = "no-captions"

            if caption_url:
                ext = detect_caption_extension(caption_url)
                safe_name = sanitize_filename_component(session_name)
                caption_file = f"{session_id}_{safe_name[:80]}{ext}"
                panopto.download_caption_file(caption_url, captions_dir / caption_file)
                status = "downloaded"

            enriched.append(
                PanoptoCaptionRecord(
                    **record,
                    session_id=session_id,
                    session_name=session_name,
                    viewer_url=urls.get("ViewerUrl") or urls.get("EmbedUrl"),
                    caption_download_url=caption_url or None,
                    caption_file=caption_file,
                    status=status,
                )
            )
        except Exception as err:
            # Exceptions can contain signed URLs or echoed credentials.
            logger.error("Panopto caption fetch failed (%s)", type(err).__name__)
            enriched.append(
                PanoptoCaptionRecord(
                    **record,
                    session_id=session_id,
                    status="error",
                    caption_file=None,
                    error=type(err).__name__,
                )
            )

    enriched.sort(key=lambda r: (r.unit_name, r.session_name or r.title))
    return enriched


def write_outputs(
    records: Sequence[Mapping[str, Any] | PanoptoCaptionRecord],
    output_dir: Path,
    course_id: int | str,
) -> None:
    """Save the JSON video manifest and Markdown caption summary."""
    serializable = [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in records]
    (output_dir / "panopto_videos.json").write_text(
        json.dumps(serializable, indent=2), encoding="utf-8"
    )

    lines = [
        "# Panopto Captions",
        "",
        f"Processed {len(serializable)} unique Panopto sessions from course {course_id}.",
        "",
        "| Unit | Title | Status | Caption File |",
        "|------|-------|--------|--------------|",
    ]
    for record in serializable:
        unit_cell = (record.get("unit_name") or "").replace("|", "-")
        title_cell = (record.get("session_name") or record.get("title") or "").replace("|", "-")
        status_cell = record.get("status") or ""
        file_cell = record.get("caption_file") or ""
        lines.append(f"| {unit_cell} | {title_cell} | {status_cell} | {file_cell} |")

    (output_dir / "panopto_captions.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def recorded_at(session: Mapping[str, Any] | PanoptoSession) -> str:
    """Sort key for a session's start time, empty when unknown."""
    return session.get("StartTime") or ""


def describe_session(session: Mapping[str, Any] | PanoptoSession) -> str:
    """Format one single-line summary for a Panopto session."""
    started = recorded_at(session)[:10] or "----------"
    minutes = round((session.get("Duration") or 0) / 60)
    return f"{started}  {session.get('Id', '')}  {minutes:>4}m  {(session.get('Name') or '')[:52]}"


describe = describe_session


def find_panopto_sessions(
    panopto: PanoptoClient,
    *,
    folder_id: str | None = None,
    query: str | None = None,
) -> list[PanoptoSession]:
    """Fetch sessions by folder ID or search query, sorted newest first."""
    sessions = (
        panopto.list_folder_sessions(folder_id)
        if folder_id
        else panopto.search_sessions(query or "")
    )
    return sorted(sessions, key=recorded_at, reverse=True)


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE ``(code_verifier, code_challenge)`` pair."""
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


def build_oauth_debug_info(
    base_url: str = DEFAULT_PANOPTO_BASE_URL,
    redirect_uri: str = DEFAULT_OAUTH_REDIRECT_URI,
    scopes: str = DEFAULT_OAUTH_SCOPES,
    client_type: str = "user",
) -> PanoptoOAuthDebugInfo | None:
    """Build redacted OAuth2 PKCE settings without exposing credentials."""
    credentials = load_panopto_client_credentials(client_type)
    if credentials is None:
        return None
    _, challenge = generate_pkce_pair()
    safe_parameters = {
        "client_id": "[REDACTED]",
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": "[REDACTED]",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    normalized_base = base_url.rstrip("/")
    auth_url = f"{normalized_base}/oauth2/connect/authorize"
    return PanoptoOAuthDebugInfo(
        credential_source=credentials.source,
        authorization_url=f"{auth_url}?{urllib.parse.urlencode(safe_parameters)}",
        parameters=safe_parameters,
    )


def exchange_oauth_authorization_code(
    base_url: str,
    authorization_code: str,
    code_verifier: str,
    *,
    redirect_uri: str = DEFAULT_OAUTH_REDIRECT_URI,
    credentials: PanoptoClientCredentials | None = None,
    token_file: Path | None = None,
) -> dict[str, Any] | None:
    """Exchange an OAuth2 authorization code via ``PanoptoClient._make_request``."""
    resolved = credentials or load_panopto_client_credentials("user")
    if resolved is None:
        return None
    client = PanoptoClient(
        base_url,
        auth_strategy=PanoptoTokenAuth("oauth-code-exchange-placeholder"),
    )
    token_data = {
        "grant_type": "authorization_code",
        "code": authorization_code,
        "redirect_uri": redirect_uri,
        "client_id": resolved.client_id,
        "client_secret": resolved.client_secret,
        "code_verifier": code_verifier,
    }
    try:
        response = client._make_request(
            "POST",
            client.token_endpoint,
            data=token_data,
            include_auth_headers=False,
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    payload: dict[str, Any] = response.json()
    if token_file is not None:
        descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.chmod(token_file, 0o600)
            json.dump(payload, handle, indent=2)
    return payload


def probe_password_grant_folder_sessions(
    base_url: str,
    folder_id: str,
    *,
    username: str | None = None,
    password: str | None = None,
    credentials: PanoptoClientCredentials | None = None,
) -> list[PanoptoSession] | None:
    """Authenticate with password grant, probe endpoints, and read ``folder_id``."""
    resolved = credentials or load_panopto_client_credentials("user")
    if resolved is None:
        return None
    resolved_user = username if username is not None else os.getenv("PANOPTO_USERNAME", "")
    resolved_pass = password if password is not None else os.getenv("PANOPTO_PASSWORD", "")
    if not resolved_user.strip() or not resolved_pass:
        return None

    strategy = PanoptoPasswordGrantAuth(resolved, resolved_user, resolved_pass)
    client = PanoptoClient(base_url, auth_strategy=strategy, folder_id=folder_id)
    try:
        strategy.get_access_token(client)
        for endpoint in DIAGNOSTIC_ENDPOINTS:
            client._make_request("GET", f"{client.base_url}{endpoint}")
        response = client._make_request(
            "GET", f"{client.api_base_url}/folders/{folder_id}/sessions"
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        raw_sessions = payload.get("Results", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_sessions, list):
            return []
        return [PanoptoSession.from_api_payload(item) for item in raw_sessions]
    except (RuntimeError, requests.RequestException, ValueError):
        return None
