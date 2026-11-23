"""Tests for cantrips/scan_secrets.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cantrips import scan_secrets  # noqa: E402


def _make_fake_github_token() -> str:
    """Build a synthetic token at runtime so this test file stays clean."""
    prefix = "gh" + "p_"
    body = "".join(f"A{idx}b{idx}" for idx in range(9))
    return prefix + body


def test_secrets_stage_fails_on_planted_fake_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Acceptance 3: planting a fake token in a temp file fails the secrets stage."""
    baseline_path = tmp_path / ".secrets.baseline"
    empty_baseline = scan_secrets.run_detect_secrets([], None)
    empty_baseline["results"] = {}
    baseline_path.write_text(json.dumps(empty_baseline), encoding="utf-8")

    planted_file = tmp_path / "planted_secret.py"
    planted_file.write_text(
        f'TOKEN = "{_make_fake_github_token()}"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(scan_secrets, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(scan_secrets, "_BASELINE_PATH", baseline_path)
    monkeypatch.setattr(
        scan_secrets,
        "list_candidate_files",
        lambda: ["planted_secret.py"],
    )
    monkeypatch.setattr(sys, "argv", ["scan_secrets.py"])

    with pytest.raises(SystemExit) as exc_info:
        scan_secrets.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr().out
    assert "NEW        planted_secret.py:1" in captured
    assert "SCAN FAILED - unreviewed secret(s)." in captured


def test_find_unreviewed_findings_distinguishes_new_unaudited_and_reviewed() -> None:
    hash_field = "hashed_" + "secret"
    flag_field = "is_" + "secret"
    candidate = {
        "results": {
            "a.py": [
                {
                    "type": "Secret Keyword",
                    hash_field: "hash_ok",
                    "line_number": 10,
                },
                {
                    "type": "Secret Keyword",
                    hash_field: "hash_unreviewed",
                    "line_number": 20,
                },
                {
                    "type": "Secret Keyword",
                    hash_field: "hash_new",
                    "line_number": 30,
                },
            ]
        }
    }
    baseline = {
        "results": {
            "a.py": [
                {
                    "type": "Secret Keyword",
                    hash_field: "hash_ok",
                    "line_number": 10,
                    flag_field: False,
                },
                {
                    "type": "Secret Keyword",
                    hash_field: "hash_unreviewed",
                    "line_number": 20,
                    flag_field: True,
                },
            ]
        }
    }

    problems = scan_secrets.find_unreviewed_findings(candidate, baseline)
    assert problems == [
        "NEW        a.py:30 (Secret Keyword)",
        "UNAUDITED  a.py:20 (Secret Keyword)",
    ]


def test_update_baseline_preserves_labels_and_normalizes_filter_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_path = tmp_path / ".secrets.baseline"
    monkeypatch.setattr(scan_secrets, "_BASELINE_PATH", baseline_path)
    hash_field = "hashed_" + "secret"
    flag_field = "is_" + "secret"

    previous = {
        "results": {
            "dir\\file.py": [
                {
                    "type": "Secret Keyword",
                    hash_field: "abc",
                    "line_number": 5,
                    flag_field: False,
                }
            ]
        }
    }
    candidate = {
        "filters_used": [
            {
                "path": "detect_secrets.filters.common.is_baseline_file",
                "filename": "/tmp/scratch/candidate.baseline",
            }
        ],
        "results": {
            "dir/file.py": [
                {
                    "type": "Secret Keyword",
                    hash_field: "abc",
                    "line_number": 8,
                }
            ]
        },
    }

    scan_secrets.update_baseline(candidate, previous)
    written = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert written["results"]["dir/file.py"][0][flag_field] is False
    assert (
        written["filters_used"][0]["filename"] == ".secrets.baseline"
    )


def test_list_candidate_files_ignores_worktrees_and_missing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_file = tmp_path / "tracked.py"
    real_file.write_text("x = 1\n", encoding="utf-8")
    worktree_dir = tmp_path / ".worktrees" / "card"
    worktree_dir.mkdir(parents=True)
    (worktree_dir / "nested.py").write_text("y = 2\n", encoding="utf-8")

    fake_stdout = b"tracked.py\0deleted.py\0.worktrees/card/nested.py\0"
    monkeypatch.setattr(scan_secrets, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=fake_stdout, stderr=b""
        ),
    )

    assert scan_secrets.list_candidate_files() == ["tracked.py"]


def test_scan_in_batches_merges_disjoint_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(files: list[str], baseline: Path | None) -> dict:
        calls.append(files)
        return {
            "results": {
                name: [{"type": "Hex", "hashed_secret": name, "line_number": 1}]
                for name in files
            }
        }

    monkeypatch.setattr(scan_secrets, "_MAX_BATCH_ARGUMENT_CHARACTERS", 10)
    monkeypatch.setattr(scan_secrets, "run_detect_secrets", fake_run)

    merged = scan_secrets.scan_in_batches(["alpha.py", "bravo.py", "charlie.py"], None)
    assert len(calls) == 3
    assert set(merged["results"].keys()) == {"alpha.py", "bravo.py", "charlie.py"}
