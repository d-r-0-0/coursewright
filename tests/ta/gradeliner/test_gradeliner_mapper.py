"""
Tests for GradeLiner Mapper (``gradeliner/mapper.py``)
=====================================================

PURPOSE:
--------
Verifies the fuzzy matching logic and cache persistence of the MappingManager.
Ensures that LLM responses are correctly parsed and that the course folder
structure is created as expected.
"""

from unittest.mock import MagicMock, patch

import pytest

from coursewright.core.canvas.models import Assignment, CanvasUser
from coursewright.ta.gradeliner.mapper import MappingManager
from coursewright.ta.gradeliner.models import (
    AssignmentMapCache,
    AssignmentMapping,
)


@pytest.fixture
def mock_llm_client():
    """Returns a mocked LLMClient."""
    return MagicMock()


@pytest.fixture
def mapper(mock_llm_client, tmp_path):
    """
    Returns a MappingManager instance pointing to a temporary directory.
    The export root is pointed at pytest's tmp_path.
    """
    return MappingManager("TEST_COURSE_101", mock_llm_client, export_root=tmp_path)


def test_default_cache_uses_private_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "private"
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(workspace))
    manager = MappingManager("COURSE-101", None)
    assert manager.export_directory == workspace / "exports" / "COURSE-101"


def test_cache_requires_private_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    with pytest.raises(ValueError, match="private workspace"):
        MappingManager("COURSE-101", None)
    assert not (tmp_path / "exports").exists()


@pytest.mark.parametrize("course_code", [".", "..", "../outside", ""])
def test_course_folder_stays_inside_export_root(tmp_path, course_code):
    manager = MappingManager(course_code, None, export_root=tmp_path)
    assert manager.export_directory.parent == tmp_path


# =============================================================================
# 1.  LLM RESPONSE PARSING
# =============================================================================


class TestLLMResponseParsing:
    """Tests the TSV parsing of LLM outputs."""

    def test_parses_assignment_mapping_correctly(self, mapper):
        """Valid TSV lines should result in AssignmentMapping objects."""
        response_text = (
            "NetCol1\tCanvasName1\t9001\thigh\n"
            "NetCol2\tCanvasName2\t9002\tmedium\n"
            "Metadata\tUNMAPPED\t0\tlow"
        )
        assignments = [
            Assignment(id=9001, name="CanvasName1", points_possible=100),
            Assignment(id=9002, name="CanvasName2", points_possible=50),
        ]

        mappings = mapper._parse_assignment_llm_response(response_text, assignments)

        assert len(mappings) == 2
        assert mappings[0].netacad_column == "NetCol1"
        assert mappings[0].canvas_assignment_id == 9001
        assert mappings[0].confidence == "high"
        assert mappings[1].canvas_points_possible == 50

    def test_parses_student_mapping_correctly(self, mapper):
        """Valid student TSV lines should result in StudentMapping objects."""
        response_text = (
            "Net Name\tnet@example.invalid\tCanvas Name\t12345\temail_match\thigh\n"
            "Ghost\tghost@example.invalid\tUNMATCHED\t0\tnone\tlow"
        )
        roster = [CanvasUser(id=12345, name="Canvas Name", login_id="cname")]

        mappings = mapper._parse_student_llm_response(response_text, roster)

        assert len(mappings) == 1
        assert mappings[0].netacad_name == "Net Name"
        assert mappings[0].canvas_user_id == 12345
        assert mappings[0].match_method == "email_match"


# =============================================================================
# 2.  CACHE PERSISTENCE
# =============================================================================


class TestCachePersistence:
    """Verifies reading and writing mapping caches to disk."""

    def test_save_and_load_assignment_cache(self, mapper):
        """A saved cache should be recoverable from disk."""
        mapping = AssignmentMapping(
            netacad_column="Ex1",
            canvas_assignment_name="Hwk1",
            canvas_assignment_id=999,
            canvas_points_possible=100,
        )
        cache = AssignmentMapCache(
            course_id=123, canvas_course_name="Test Course", mappings=[mapping]
        )

        mapper.save_assignment_cache(cache)
        loaded = mapper.load_assignment_cache()

        assert loaded is not None
        assert loaded.course_id == 123
        assert len(loaded.mappings) == 1
        assert loaded.mappings[0].netacad_column == "Ex1"

    def test_returns_none_for_missing_cache(self, mapper):
        """Loading a non-existent cache should return None, not raise."""
        assert mapper.load_assignment_cache() is None
        assert mapper.load_student_cache() is None


# =============================================================================
# 3.  PROPOSAL GENERATION
# =============================================================================


class TestProposalGeneration:
    """Verifies that the LLM is called with formatted prompts."""

    @patch("coursewright.ta.gradeliner.mapper.MappingManager._load_prompt")
    def test_propose_assignment_mapping_calls_llm(self, mock_load, mapper, mock_llm_client):
        """The LLM should be invoked with the expected template context."""
        mock_load.side_effect = lambda x: "Template with {netacad_columns} and {canvas_assignments}"
        mock_llm_client.generate_sync.return_value = MagicMock(content="C1\tA1\t101\thigh")

        netacad_cols = ["C1"]
        canvas_assignments = [Assignment(id=101, name="A1", points_possible=100)]

        results = mapper.propose_assignment_mapping(netacad_cols, canvas_assignments)

        assert len(results) == 1
        assert mock_llm_client.generate_sync.called
        # Check that temperature is 0.0 for deterministic matching
        args, kwargs = mock_llm_client.generate_sync.call_args
        assert kwargs["temperature"] == 0.0
        assert kwargs["max_tokens"] == 4000

    @patch("coursewright.ta.gradeliner.mapper.MappingManager._load_prompt")
    def test_mapping_token_budget_respects_larger_config(self, mock_load, mapper, mock_llm_client):
        """Mapping calls should honor larger user-configured token budgets."""
        mock_load.side_effect = lambda x: "Template with {netacad_columns} and {canvas_assignments}"
        mock_llm_client.config.max_tokens = 8000
        mock_llm_client.generate_sync.return_value = MagicMock(content="C1	A1	101	high")

        mapper.propose_assignment_mapping(
            ["C1"],
            [Assignment(id=101, name="A1", points_possible=100)],
        )

        _args, kwargs = mock_llm_client.generate_sync.call_args
        assert kwargs["max_tokens"] == 8000


# =============================================================================
# 4.  STALE-NAME RECONCILIATION
# =============================================================================


class TestReconcileAssignmentNames:
    """
    Tests for ``MappingManager.reconcile_assignment_names``.

    Verifies that cached ``canvas_assignment_name`` values are updated
    when the live Canvas name differs, while IDs and unrelated mappings
    are left untouched.
    """

    def _make_cache(self, mapper, mappings: list[AssignmentMapping]) -> None:
        """Helper: write a fresh cache to the mapper's cache path."""
        cache = AssignmentMapCache(
            course_id=42,
            canvas_course_name="Test Course",
            mappings=mappings,
        )
        mapper.save_assignment_cache(cache)

    def test_stale_name_is_updated_and_cache_is_saved(self, mapper):
        """
        When the Canvas assignment was renamed, reconcile_assignment_names
        should update the cached name and persist the change to disk.
        """
        original_mapping = AssignmentMapping(
            netacad_column="U01T01 PTSA",
            canvas_assignment_name="U01T01: Old Verbose Name (v6)",
            canvas_assignment_id=1001,
            canvas_points_possible=100.0,
        )
        self._make_cache(mapper, [original_mapping])

        # Live Canvas now has a friendlier name for the same ID.
        live_assignments = [
            Assignment(
                id=1001,
                name="U01T01: Introduction to Networks (v7)",
                points_possible=100.0,
            )
        ]

        changes = mapper.reconcile_assignment_names(live_assignments)

        assert len(changes) == 1
        assert "Old Verbose Name" in changes[0]
        assert "Introduction to Networks" in changes[0]

        # Re-load the cache from disk to confirm the write happened.
        reloaded = mapper.load_assignment_cache()
        assert reloaded is not None
        assert reloaded.mappings[0].canvas_assignment_name == (
            "U01T01: Introduction to Networks (v7)"
        )

    def test_matching_name_leaves_cache_unchanged(self, mapper):
        """
        When the live name already matches the cached name, no write
        should occur and an empty list should be returned.
        """
        name = "U02T01: Networking Basics"
        mapping = AssignmentMapping(
            netacad_column="U02T01 Basics",
            canvas_assignment_name=name,
            canvas_assignment_id=1002,
            canvas_points_possible=50.0,
        )
        self._make_cache(mapper, [mapping])

        live_assignments = [Assignment(id=1002, name=name, points_possible=50.0)]

        changes = mapper.reconcile_assignment_names(live_assignments)

        assert changes == []

    def test_deleted_canvas_assignment_id_is_left_alone(self, mapper):
        """
        If a Canvas assignment ID no longer appears in the live list
        (e.g. it was deleted), the cached mapping should be unchanged.
        """
        mapping = AssignmentMapping(
            netacad_column="U03D01 Discussion",
            canvas_assignment_name="U03D01: Forum",
            canvas_assignment_id=9999,
            canvas_points_possible=25.0,
        )
        self._make_cache(mapper, [mapping])

        # ID 9999 is absent — maybe it was deleted from Canvas.
        live_assignments = [Assignment(id=1003, name="Some Other Assignment", points_possible=10.0)]

        changes = mapper.reconcile_assignment_names(live_assignments)

        assert changes == []
        # Cache should be unmodified on disk.
        reloaded = mapper.load_assignment_cache()
        assert reloaded is not None
        assert reloaded.mappings[0].canvas_assignment_name == "U03D01: Forum"

    def test_no_cache_returns_empty_list(self, mapper):
        """
        When there is no assignment cache file at all, reconcile should
        return an empty list without raising.
        """
        changes = mapper.reconcile_assignment_names(
            [Assignment(id=1, name="Anything", points_possible=100.0)]
        )
        assert changes == []


# =============================================================================
# 5.  UNIT-CODE PRE-MATCHING
# =============================================================================


class TestUnitCodePreMatch:
    """
    Tests for ``_extract_unit_code`` and the unit-code pre-pass in
    ``propose_assignment_mapping``.
    """

    def test_extract_unit_code_finds_code(self, mapper):
        """Codes at the start (and anywhere) of the string are found."""
        assert mapper._extract_unit_code("U01T01: Intro to Networks") == "U01T01"
        assert mapper._extract_unit_code("U03D01 Discussion") == "U03D01"
        assert mapper._extract_unit_code("Module U02E01 Exam") == "U02E01"

    def test_extract_unit_code_returns_none_for_plain_text(self, mapper):
        """Strings without a unit code should return None."""
        assert mapper._extract_unit_code("Module 1 Exam") is None
        assert mapper._extract_unit_code("Final Assessment") is None
        assert mapper._extract_unit_code("") is None

    def test_extract_unit_code_is_case_insensitive(self, mapper):
        """Lower-case inputs should still match and be returned upper-cased."""
        assert mapper._extract_unit_code("u01t01: something") == "U01T01"

    @patch("coursewright.ta.gradeliner.mapper.MappingManager._load_prompt")
    def test_pre_pass_matches_without_llm_call(self, mock_load, mapper, mock_llm_client):
        """
        When all NetAcad columns match Canvas assignments via unit code,
        the LLM should NOT be called.
        """
        mock_load.return_value = "unused"

        netacad_cols = ["U01T01 PTSA", "U02T01 Skills Exam"]
        canvas_assignments = [
            Assignment(id=201, name="U01T01: Intro Assessment", points_possible=100),
            Assignment(id=202, name="U02T01: Routing Skills", points_possible=50),
        ]

        results = mapper.propose_assignment_mapping(netacad_cols, canvas_assignments)

        assert len(results) == 2
        assert results[0].canvas_assignment_id == 201
        assert results[1].canvas_assignment_id == 202
        mock_llm_client.generate_sync.assert_not_called()

    @patch("coursewright.ta.gradeliner.mapper.MappingManager._load_prompt")
    def test_unmatched_columns_fall_through_to_llm(self, mock_load, mapper, mock_llm_client):
        """
        Columns with no unit code (or no Canvas counterpart) should be
        forwarded to the LLM.  Deterministically matched columns should
        NOT appear in the LLM prompt.
        """
        mock_load.side_effect = lambda x: "Template with {netacad_columns} and {canvas_assignments}"
        mock_llm_client.generate_sync.return_value = MagicMock(
            content="Generic Exam\tGeneric Canvas\t301\tmedium"
        )

        netacad_cols = ["U01T01 PTSA", "Generic Exam"]
        canvas_assignments = [
            Assignment(id=201, name="U01T01: Intro", points_possible=100),
            Assignment(id=301, name="Generic Canvas", points_possible=75),
        ]

        results = mapper.propose_assignment_mapping(netacad_cols, canvas_assignments)

        # Both columns should be mapped: one via unit code, one via LLM.
        assert len(results) == 2
        ids = {r.canvas_assignment_id for r in results}
        assert ids == {201, 301}

        # LLM should have been called, but only for the unmatched column.
        mock_llm_client.generate_sync.assert_called_once()
        call_prompt = mock_llm_client.generate_sync.call_args[1]["prompt"]
        # The pre-matched column should NOT appear in the LLM prompt.
        assert "U01T01 PTSA" not in call_prompt
