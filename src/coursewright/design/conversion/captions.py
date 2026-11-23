"""Turn Panopto SRT caption files into readable plain-text transcripts.

SRT is the right thing to archive but close to useless as reading
material: roughly two thirds of it is cue indices and timestamps.
This rebuilds prose: strips indices and timestamps, drops cues that
repeat the previous one verbatim (machine captions stutter when a
speaker pauses mid-sentence), reflows fragments into sentences and
paragraphs, and writes one .txt per session under a header naming the
source.

The output is machine transcription and says so in its own header.
Layout: ``<dir>/panopto_captions/*.txt`` in, ``<dir>/transcripts/*.txt``
out.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

CAPTIONS_SUBDIR = "panopto_captions"
TRANSCRIPTS_SUBDIR = "transcripts"

TIMESTAMP = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->")
CUE_INDEX = re.compile(r"^\d+$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# Panopto prepends its own disclaimer as the first cue. It is not
# something anyone said, so it does not belong in the prose.
PANOPTO_BOILERPLATE = re.compile(r"^\[Auto-generated transcript[^\]]*\]\s*", re.I)

SENTENCES_PER_PARAGRAPH = 5
WRAP_WIDTH = 78


def parse_srt_cues(text: str) -> list[str]:
    """Pull the spoken text out of an SRT file, one string per cue."""
    cues: list[str] = []
    current: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                cues.append(" ".join(current))
                current = []
            continue
        if TIMESTAMP.match(line):
            continue
        # A bare number is a cue index only when no text has been read
        # yet for this cue; "1995" mid-sentence must survive.
        if CUE_INDEX.match(line) and not current:
            continue
        current.append(line)

    if current:
        cues.append(" ".join(current))
    return cues


def drop_stutters(cues: list[str]) -> list[str]:
    """Remove cues that repeat the previous one verbatim."""
    kept: list[str] = []
    for cue in cues:
        if kept and cue.strip().lower() == kept[-1].strip().lower():
            continue
        kept.append(cue)
    return kept


def to_paragraphs(cues: list[str]) -> str:
    """Reflow caption fragments into wrapped paragraphs."""
    prose = " ".join(" ".join(cues).split())
    prose = PANOPTO_BOILERPLATE.sub("", prose)
    sentences = SENTENCE_END.split(prose)

    paragraphs = [
        " ".join(sentences[index : index + SENTENCES_PER_PARAGRAPH])
        for index in range(0, len(sentences), SENTENCES_PER_PARAGRAPH)
    ]
    return "\n\n".join(
        textwrap.fill(paragraph, width=WRAP_WIDTH) for paragraph in paragraphs if paragraph.strip()
    )


def describe_source(path: Path) -> tuple[str, str]:
    """Recover the session id and readable title from the filename."""
    stem = path.stem
    session_id, _, remainder = stem.partition("_")
    return session_id, remainder.replace("_", " ").strip() or stem


def convert(source: Path, destination_dir: Path, *, viewer_url_prefix: str = "") -> Path:
    """Write one transcript and return its path.

    `viewer_url_prefix` is the Panopto viewer address up to and
    including ``id=``; when empty, no "Watch:" line is written.
    """
    session_id, title = describe_source(source)
    cues = drop_stutters(parse_srt_cues(source.read_text(encoding="utf-8")))

    watch_line = f"Watch: {viewer_url_prefix}{session_id}\n" if viewer_url_prefix else ""
    header = (
        f"{title}\n"
        f"{'=' * len(title)}\n\n"
        f"Panopto session: {session_id}\n"
        f"{watch_line}\n"
        "Machine-generated captions, reflowed into paragraphs. Expect\n"
        "transcription errors -- verify anything before quoting it.\n\n"
        f"{'-' * WRAP_WIDTH}\n\n"
    )

    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{source.stem}.txt"
    destination.write_text(header + to_paragraphs(cues) + "\n", encoding="utf-8")
    return destination


def convert_caption_folder(materials_dir: Path, *, viewer_url_prefix: str = "") -> list[Path]:
    """Convert every caption file under a materials folder.

    Raises FileNotFoundError when there is no caption folder or no
    caption file in it.
    """
    captions_dir = materials_dir / CAPTIONS_SUBDIR
    if not captions_dir.is_dir():
        raise FileNotFoundError(f"No {CAPTIONS_SUBDIR}/ folder in {materials_dir}")
    sources = sorted(captions_dir.glob("*.txt"))
    if not sources:
        raise FileNotFoundError(f"No caption files in {captions_dir}")
    transcripts_dir = materials_dir / TRANSCRIPTS_SUBDIR
    return [
        convert(source, transcripts_dir, viewer_url_prefix=viewer_url_prefix) for source in sources
    ]
