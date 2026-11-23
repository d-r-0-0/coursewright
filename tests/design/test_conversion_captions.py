"""Tests for Panopto SRT caption reflow and single-session SRT fetch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from coursewright.core.traffic import subscribe, unsubscribe
from coursewright.design.conversion.captions import (
    CAPTIONS_SUBDIR,
    TRANSCRIPTS_SUBDIR,
    convert,
    convert_caption_folder,
    describe_source,
    drop_stutters,
    parse_srt_cues,
    to_paragraphs,
)
from coursewright.design.conversion.srt_fetch import (
    PanoptoUrls,
    caption_filename,
    fetch_session_captions,
    fetch_session_title,
    fetch_srt_text,
    is_session_id,
    sanitize_filename_component,
)


def test_captions_reflow_stutter_removal_and_folder_conversion(tmp_path: Path) -> None:
    srt_sample = (
        "1\n"
        "00:00:00,000 --> 00:00:03,000\n"
        "[Auto-generated transcript. Accuracy may vary.]\n\n"
        "2\n"
        "00:00:03,000 --> 00:00:05,000\n"
        "Welcome to the lecture\n\n"
        "3\n"
        "00:00:05,000 --> 00:00:07,000\n"
        "Welcome to the lecture\n\n"
        "4\n"
        "00:00:07,000 --> 00:00:11,000\n"
        "founded in\n"
        "1995\n"
        "about network packets.\n"
    )
    cues = parse_srt_cues(srt_sample)
    assert len(cues) == 4
    deduped = drop_stutters(cues)
    assert len(deduped) == 3
    paragraphs = to_paragraphs(deduped)
    assert "Auto-generated transcript" not in paragraphs
    assert "Welcome to the lecture founded in 1995 about network packets." in paragraphs

    captions_dir = tmp_path / CAPTIONS_SUBDIR
    captions_dir.mkdir()
    caption_file = captions_dir / "11111111-2222-3333-4444-555555555555_Unit_1_Intro.txt"
    caption_file.write_text(srt_sample, encoding="utf-8")

    session_id, title = describe_source(caption_file)
    assert session_id == "11111111-2222-3333-4444-555555555555"
    assert title == "Unit 1 Intro"

    outputs = convert_caption_folder(
        tmp_path, viewer_url_prefix="https://panopto.example.edu/Panopto/Pages/Viewer.aspx?id="
    )
    assert len(outputs) == 1
    transcript_text = outputs[0].read_text(encoding="utf-8")
    assert "Unit 1 Intro" in transcript_text
    expected_watch = "Watch: https://panopto.example.edu/Panopto/Pages/Viewer.aspx?id=11111111"
    assert expected_watch in transcript_text
    assert convert is not None


def test_convert_caption_folder_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        convert_caption_folder(tmp_path)
    (tmp_path / CAPTIONS_SUBDIR).mkdir()
    with pytest.raises(FileNotFoundError):
        convert_caption_folder(tmp_path)


def test_srt_fetch_pipeline_and_traffic_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "df7ab5f9-a5fe-47bc-ace3-b4c10011e14c"
    assert is_session_id(session_id)
    assert not is_session_id("not-a-uuid")
    assert sanitize_filename_component("Lecture 1: Intro/Overview?") == "Lecture 1_ Intro_Overview"
    assert caption_filename(session_id, "Lecture 1") == f"{session_id}_Lecture 1.txt"

    srt_body = "1\r\n00:00:00,000 --> 00:00:02,000\r\nHello class.\r\n"

    def fake_request(session: object, method: str, url: str, **kwargs: object) -> MagicMock:
        assert method == "GET"
        assert kwargs["timeout"] == 30
        response = MagicMock()
        response.status_code = 200
        response.url = url
        response.raise_for_status = MagicMock()
        if "Viewer.aspx" in url:
            response.text = "<html><head><title>Week 1 &amp; Intro</title></head></html>"
        else:
            response.text = srt_body
        return response

    monkeypatch.setattr(requests.Session, "request", fake_request)

    events: list[object] = []
    subscribe(events.append)
    try:
        result = fetch_session_captions("https://panopto.example.edu", tmp_path, session_id)
        second = fetch_session_captions("https://panopto.example.edu", tmp_path, session_id)
    finally:
        unsubscribe(events.append)

    assert result.title == "Week 1 & Intro"
    assert result.cue_count == 1
    assert result.newly_inventoried is True
    assert second.newly_inventoried is False
    assert result.caption_path.exists()
    assert (tmp_path / TRANSCRIPTS_SUBDIR / result.caption_path.name).exists()
    assert len(events) == 8


def test_srt_fetch_rejects_login_redirect_and_non_srt(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = PanoptoUrls.from_base("https://panopto.example.edu")
    session_id = "df7ab5f9-a5fe-47bc-ace3-b4c10011e14c"

    redirect_response = MagicMock(
        status_code=200,
        url="https://panopto.example.edu/Panopto/Pages/Auth/Login.aspx",
        text="<title>Panopto</title>",
    )
    redirect_response.raise_for_status = MagicMock()
    def fake_request(
        session: object, method: str, url: str, **kwargs: object
    ) -> MagicMock:
        return redirect_response

    monkeypatch.setattr(requests.Session, "request", fake_request)
    with pytest.raises(RuntimeError, match="login page"):
        fetch_session_title(urls, session_id)

    html_response = MagicMock(
        status_code=200,
        url="https://panopto.example.edu/srt",
        text="<html>login required</html>",
    )
    html_response.raise_for_status = MagicMock()
    with pytest.raises(RuntimeError, match="did not return SRT cues"):
        fetch_srt_text(urls, session_id)
