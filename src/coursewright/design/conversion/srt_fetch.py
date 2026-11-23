"""Pull the machine captions for ONE Panopto session by its id.

Uses the public endpoint the Panopto viewer's "download captions"
button hits (``GenerateSRT.ashx``).  For sessions shared with the whole
organization it answers without a token.  If it answers with HTML
instead of cues, the session is private, and this module says so
rather than saving a login page as a transcript.

The Panopto host is never hard-coded: callers pass `panopto_base`
(for example ``https://example.hosted.panopto.com``).  Every round
trip publishes a traffic event (AGENTS.md 3.16).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from coursewright.design.conversion.captions import (
    CAPTIONS_SUBDIR,
    TRANSCRIPTS_SUBDIR,
    convert,
)
from coursewright.design.media.panopto_api import PanoptoClient
from coursewright.design.media.panopto_auth import PanoptoNoAuth

SRT_LANGUAGE = "English_USA"
INVENTORY_FILE_NAME = "panopto_videos.json"
MAX_TITLE_LENGTH = 80

TITLE_TAG = re.compile(r"<title>(.*?)</title>", re.I | re.S)
SESSION_ID_SHAPE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
# An SRT file opens with a cue index and a timestamp range.  Anything
# else -- an HTML login page, an empty body -- is not captions.
SRT_OPENING = re.compile(r"^\s*1\s*\r?\n\d{2}:\d{2}:\d{2},\d{3}\s*-->")
UNSAFE_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


@dataclass(frozen=True)
class PanoptoUrls:
    """The four addresses built from one organisation's Panopto host."""

    viewer_prefix: str
    embed_prefix: str
    srt_prefix: str

    @classmethod
    def from_base(cls, panopto_base: str) -> PanoptoUrls:
        pages = f"{panopto_base.rstrip('/')}/Panopto/Pages"
        return cls(
            viewer_prefix=f"{pages}/Viewer.aspx?id=",
            embed_prefix=f"{pages}/Embed.aspx?id=",
            srt_prefix=f"{pages}/Transcription/GenerateSRT.ashx?id=",
        )


def is_session_id(candidate: str) -> bool:
    return bool(SESSION_ID_SHAPE.match(candidate))


def sanitize_filename_component(value: str) -> str:
    """Make text safe to use inside a file name."""
    cleaned = UNSAFE_FILENAME_CHARACTERS.sub("_", value).strip(" ._")
    return cleaned or "untitled"


def _client_for(urls: PanoptoUrls) -> PanoptoClient:
    """Create an anonymous client for the Panopto host encoded in ``urls``."""
    parsed = urlsplit(urls.viewer_prefix)
    base_url = f"{parsed.scheme}://{parsed.netloc}/Panopto"
    return PanoptoClient(base_url=base_url, auth_strategy=PanoptoNoAuth())


def fetch_session_title(urls: PanoptoUrls, session_id: str) -> str:
    """Read the session name off the public viewer page's <title>."""
    response = _client_for(urls)._make_request("GET", f"{urls.viewer_prefix}{session_id}")
    response.raise_for_status()
    # A session that is not shared org-wide redirects to a login page
    # whose <title> is just "Panopto".
    if "/Auth/" in response.url:
        raise RuntimeError(
            "Panopto redirected to its login page: this session is not "
            "viewable. Move it into the course's Panopto folder so it "
            "inherits the folder's permissions, then re-run."
        )
    match = TITLE_TAG.search(response.text)
    if not match:
        return session_id
    return html.unescape(match.group(1)).strip() or session_id


def fetch_srt_text(urls: PanoptoUrls, session_id: str) -> str:
    """Download the caption cues, refusing anything that is not SRT."""
    response = _client_for(urls)._make_request(
        "GET", f"{urls.srt_prefix}{session_id}&language={SRT_LANGUAGE}"
    )
    response.raise_for_status()
    text = response.text
    if not SRT_OPENING.search(text):
        raise RuntimeError(
            "Panopto did not return SRT cues. The session is probably not "
            "shared org-wide, or has no captions yet."
        )
    return text


def caption_filename(session_id: str, title: str) -> str:
    """Build the {id}_{Title}.txt name the bulk fetch tool would use."""
    safe_title = sanitize_filename_component(title)[:MAX_TITLE_LENGTH]
    return f"{session_id}_{safe_title}.txt"


def record_in_inventory(
    urls: PanoptoUrls,
    materials_dir: Path,
    session_id: str,
    title: str,
    caption_file: str,
) -> bool:
    """Append the session to panopto_videos.json unless already listed."""
    inventory_path = materials_dir / INVENTORY_FILE_NAME
    records: list[dict] = []
    if inventory_path.exists():
        records = json.loads(inventory_path.read_text(encoding="utf-8"))

    if any(record.get("session_id") == session_id for record in records):
        return False

    records.append(
        {
            "url": f"{urls.embed_prefix}{session_id}",
            "source_type": "manual",
            "title": title,
            "unit_name": "",
            "session_id": session_id,
            "session_name": title,
            "viewer_url": f"{urls.viewer_prefix}{session_id}",
            "caption_download_url": f"{urls.srt_prefix}{session_id}&language={SRT_LANGUAGE}",
            "caption_file": caption_file,
            "status": "downloaded",
        }
    )
    inventory_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return True


@dataclass(frozen=True)
class FetchResult:
    session_id: str
    title: str
    cue_count: int
    caption_path: Path
    transcript_path: Path
    newly_inventoried: bool


def fetch_session_captions(
    panopto_base: str, materials_dir: Path, session_id: str
) -> FetchResult:
    """Download one session's captions and reflow them into a transcript.

    Raises ValueError for a malformed id, NotADirectoryError for a
    missing materials folder, and RuntimeError / requests errors when
    Panopto refuses.
    """
    session_id = session_id.strip().lower()
    if not is_session_id(session_id):
        raise ValueError(f"{session_id!r} does not look like a Panopto session id")
    if not materials_dir.is_dir():
        raise NotADirectoryError(f"No materials folder: {materials_dir}")

    urls = PanoptoUrls.from_base(panopto_base)
    title = fetch_session_title(urls, session_id)
    srt_text = fetch_srt_text(urls, session_id)

    captions_dir = materials_dir / CAPTIONS_SUBDIR
    captions_dir.mkdir(parents=True, exist_ok=True)
    caption_file = caption_filename(session_id, title)
    caption_path = captions_dir / caption_file
    caption_path.write_text(srt_text, encoding="utf-8")

    transcript_path = convert(
        caption_path,
        materials_dir / TRANSCRIPTS_SUBDIR,
        viewer_url_prefix=urls.viewer_prefix,
    )
    newly_inventoried = record_in_inventory(urls, materials_dir, session_id, title, caption_file)
    return FetchResult(
        session_id=session_id,
        title=title,
        cue_count=srt_text.count("-->"),
        caption_path=caption_path,
        transcript_path=transcript_path,
        newly_inventoried=newly_inventoried,
    )
