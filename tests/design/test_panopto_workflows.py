"""Caption and session cantrip behavior exercised as silent library functions."""

import json
from unittest.mock import Mock

import pytest

from coursewright.design.media import panopto_workflows as workflows
from coursewright.design.media.panopto_api import PanoptoClient
from coursewright.design.media.panopto_auth import PanoptoTokenAuth

BASE_URL = "https://panopto.example.edu/Panopto"
VIEWER = BASE_URL + "/Pages/Viewer.aspx"


def test_normalization_extraction_and_deduplication():
    text = (
        f'<iframe src="{VIEWER}?instance=bb&amp;id=one#fragment"></iframe>'
        f'<a href="//panopto.example.edu/Panopto/Pages/Viewer.aspx?id=one">link</a>'
        f'<a href="{VIEWER}?id=two">another</a>'
    )
    assert workflows.extract_panopto_urls(text) == [VIEWER + "?id=one", VIEWER + "?id=two"]
    assert workflows.extract_panopto_urls("") == []


def test_canvas_scan_uses_injected_client_and_groups_pages(http_response):
    canvas = Mock(base_url="https://canvas.example.edu")
    canvas.get_modules.return_value = [
        {"name": "Unit B", "items": [{"page_url": "second"}]},
        {"name": "Unit A", "items": [{"title": "First"}]},
    ]
    canvas.get_pages.return_value = [
        {"url": "second", "title": "Second"},
        {"url": "first", "title": "First"},
    ]
    canvas._make_request.side_effect = [
        http_response(payload={"body": f'<a href="{VIEWER}?id=two">link</a>'}),
        http_response(
            payload={
                "body": f'<a href="{VIEWER}?id=one">link</a><a href="{VIEWER}?id=two">repeat</a>'
            }
        ),
    ]
    records = workflows.scan_canvas_course(canvas, 123)
    assert [record.unit_name for record in records] == ["Unit A", "Unit B"]
    assert records[0].title == "First"
    assert canvas._make_request.call_args.args == (
        "GET",
        "https://canvas.example.edu/api/v1/courses/123/pages/first",
    )


def test_folder_scan_and_caption_download_outputs(panopto_transport, http_response, tmp_path):
    client = PanoptoClient(BASE_URL, auth_strategy=PanoptoTokenAuth("example-value"))
    panopto_transport.side_effect = [
        http_response(
            payload={
                "Results": [
                    {"Id": "two", "Name": "B"},
                    {"Id": "one", "Name": "A", "FolderDetails": {"Name": "Unit"}},
                    {"Name": "Missing identifier"},
                ]
            }
        ),
        http_response(payload={"Results": []}),
        http_response(
            payload={
                "Name": "Lecture|One",
                "Urls": {"CaptionDownloadUrl": BASE_URL + "/captions/one.vtt", "ViewerUrl": VIEWER},
            }
        ),
        http_response(content=b"WEBVTT\n\n00:00.000 --> 00:01.000\nExample\n"),
        http_response(payload={"Name": "B", "Urls": None}),
    ]
    records = workflows.scan_panopto_folder(client, "configured-folder")
    assert [record.title for record in records] == ["A", "B"]
    assert records[0].url == VIEWER + "?id=one"
    enriched = workflows.download_captions(client, [*records, records[0]], tmp_path)
    assert len(enriched) == 2
    downloaded = next(record for record in enriched if record.status == "downloaded")
    assert (tmp_path / downloaded.caption_file).read_bytes().startswith(b"WEBVTT")
    assert downloaded.caption_file == "one_Lecture_One.vtt"
    assert any(record.status == "no-captions" for record in enriched)
    workflows.write_outputs(enriched, tmp_path, "configured-folder")
    manifest = json.loads((tmp_path / "panopto_videos.json").read_text())
    assert len(manifest) == 2
    assert "Lecture-One" in (tmp_path / "panopto_captions.md").read_text()


def test_invalid_url_and_failed_caption_are_reported_safely(tmp_path, caplog):
    client = Mock()
    client.get_session.side_effect = RuntimeError("private-credential")
    records = [{"url": "invalid"}, {"url": VIEWER + "?id=one"}]
    enriched = workflows.download_captions(client, records, tmp_path)
    assert [record.status for record in enriched] == ["invalid-url", "error"]
    assert enriched[1].error == "RuntimeError"
    assert "private-credential" not in caplog.text + repr(enriched)


@pytest.mark.parametrize(
    "suffix,expected", [(".SRT", ".srt"), (".vtt", ".vtt"), ("?format=text", ".txt")]
)
def test_caption_extension(suffix, expected):
    assert workflows.detect_caption_extension(BASE_URL + suffix) == expected


@pytest.mark.parametrize("use_folder", [True, False])
def test_find_sorts_newest_first_and_describes(use_folder):
    client = Mock()
    sessions = [
        {"Id": "one", "StartTime": "2024-01-01", "Name": "First", "Duration": 120},
        {"Id": "unknown"},
        {"Id": "two", "StartTime": "2024-02-01", "Name": "Second"},
    ]
    client.list_folder_sessions.return_value = sessions
    client.search_sessions.return_value = sessions
    arguments = {"folder_id": "configured"} if use_folder else {"query": "Lecture"}
    result = workflows.find_panopto_sessions(client, **arguments)
    assert [item["Id"] for item in result] == ["two", "one", "unknown"]
    if use_folder:
        client.list_folder_sessions.assert_called_once_with("configured")
    else:
        client.search_sessions.assert_called_once_with("Lecture")
    assert workflows.describe_session(sessions[0]) == "2024-01-01  one     2m  First"
