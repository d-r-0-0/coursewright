"""
Tests for the discussion assistant (``ta/discussions``)
============================================

PURPOSE:
--------
These tests verify the discussion assistant pipeline:
  - ``PostAnalyzer``: post ranking, scoring, LLM-based analysis
  - ``SimilarityChecker``: duplicate detection, threshold logic
  - ``ResponseGenerator``: response drafting, research integration

All LLM calls are mocked so tests run instantly without API keys.

WHAT WE'RE TESTING:
-------------------
1. PostAnalyzer — HTML stripping, summary generation, analysis parsing,
   rank score calculation with boosts/penalties, batch analysis
2. SimilarityChecker — text normalisation, structural similarity, LCS,
   threshold flagging, LLM-powered plagiarism check
3. ResponseGenerator — draft generation, research refinement, multiple
   response generation, file I/O helpers

Author: Claude Code Agent
Date: February 2026
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from coursewright.core.canvas.models import CanvasDiscussionEntry
from coursewright.ta.discussions.post_analyzer import (
    AnalysisFlag,
    PostAnalysis,
    PostAnalyzer,
    PostRanking,
    RiskLevel,
)
from coursewright.ta.discussions.response_generator import (
    ResponseDraft,
    ResponseGenerator,
    save_response_to_file,
    save_responses_to_directory,
)
from coursewright.ta.discussions.similarity_checker import (
    SimilarityChecker,
    SimilarityReport,
)

# =============================================================================
# HELPERS — Reusable factories for test data
# =============================================================================


def _make_mock_llm(content: str = "mock response"):
    """
    Create a mock ``LLMClient`` whose ``generate_sync`` returns
    a response with the given ``content``.
    """
    mock_llm = Mock()
    mock_response = Mock()
    mock_response.content = content
    mock_llm.generate_sync.return_value = mock_response
    return mock_llm


def _make_entry(
    entry_id: int = 1,
    user_id: int = 100,
    user_name: str = "Alice",
    message: str = "<p>Hello world</p>",
    word_count: int = 2,
    parent_id: int | None = None,
    days_ago: int = 3,
) -> CanvasDiscussionEntry:
    """Build a ``CanvasDiscussionEntry`` with sensible defaults."""
    return CanvasDiscussionEntry(
        id=entry_id,
        user_id=user_id,
        user_name=user_name,
        discussion_id=999,
        message=message,
        created_at=datetime.now() - timedelta(days=days_ago),
        word_count=word_count,
        parent_id=parent_id,
        replies=[],
    )


# =============================================================================
# POST ANALYZER — HTML Stripping
# =============================================================================


class TestPostAnalyzerStripHtml:
    """Verify ``_strip_html`` removes tags and decodes entities."""

    def test_removes_simple_tags(self):
        result = PostAnalyzer._strip_html("<p>Hello <b>world</b></p>")
        assert result == "Hello world"

    def test_decodes_html_entities(self):
        result = PostAnalyzer._strip_html("A &amp; B &lt; C &gt; D &quot;E&quot;")
        assert result == 'A & B < C > D "E"'

    def test_replaces_nbsp(self):
        result = PostAnalyzer._strip_html("Hello&nbsp;world")
        assert result == "Hello world"

    def test_collapses_whitespace(self):
        result = PostAnalyzer._strip_html("<p>Hello</p>   <p>world</p>")
        assert result == "Hello world"

    def test_empty_input_returns_empty(self):
        assert PostAnalyzer._strip_html("") == ""
        assert PostAnalyzer._strip_html(None) == ""


# =============================================================================
# POST ANALYZER — Parse Analysis Response
# =============================================================================


class TestParseAnalysisResponse:
    """Verify ``_parse_analysis_response`` extracts structured fields."""

    @pytest.fixture
    def analyzer(self):
        return PostAnalyzer(llm_client=_make_mock_llm())

    def test_extracts_merit_score(self, analyzer):
        response = "MERIT_SCORE: 8\nBRIEF_REASONING: Solid post."
        result = analyzer._parse_analysis_response(response)

        assert result["merit_score"] == 8
        assert result["depth_score"] == 0.8

    def test_clamps_merit_score_to_range(self, analyzer):
        response = "MERIT_SCORE: 15"
        result = analyzer._parse_analysis_response(response)
        assert result["merit_score"] == 10

    def test_extracts_ai_generated_risk(self, analyzer):
        response = "MERIT_SCORE: 5\nAI_GENERATED_RISK: High"
        result = analyzer._parse_analysis_response(response)

        assert result["ai_generated_risk"] == RiskLevel.HIGH
        assert AnalysisFlag.AI_GENERATED in result["flags"]

    def test_extracts_plagiarism_risk(self, analyzer):
        response = "MERIT_SCORE: 5\nPLAGIARISM_RISK: Medium"
        result = analyzer._parse_analysis_response(response)

        assert result["plagiarism_risk"] == RiskLevel.MEDIUM
        assert AnalysisFlag.PLAGIARISM in result["flags"]

    def test_extracts_boolean_flags(self, analyzer):
        response = (
            "MERIT_SCORE: 7\n"
            "INAPPROPRIATE_CONTENT: Yes\n"
            "MENTIONS_PROFESSOR: Yes\n"
            "NEEDS_RESPONSE: Yes"
        )
        result = analyzer._parse_analysis_response(response)

        assert result["has_inappropriate_content"] is True
        assert result["mentions_professor"] is True
        assert result["needs_response"] is True
        assert AnalysisFlag.INAPPROPRIATE in result["flags"]
        assert AnalysisFlag.MENTIONS_PROFESSOR in result["flags"]
        assert AnalysisFlag.NEEDS_RESPONSE in result["flags"]

    def test_extracts_reasoning(self, analyzer):
        response = "MERIT_SCORE: 6\nBRIEF_REASONING: The post shows depth."
        result = analyzer._parse_analysis_response(response)

        assert result["reasoning"] == "The post shows depth."

    def test_returns_defaults_for_empty_response(self, analyzer):
        result = analyzer._parse_analysis_response("")

        assert result["merit_score"] == 5
        assert result["flags"] == []


# =============================================================================
# POST ANALYZER — Rank Score Calculation
# =============================================================================


class TestCalculateRankScore:
    """Verify ``_calculate_rank_score`` with boosts and penalties."""

    @pytest.fixture
    def analyzer(self):
        return PostAnalyzer(llm_client=_make_mock_llm())

    def _make_analysis(self, **overrides) -> PostAnalysis:
        defaults = {
            "post_id": 1,
            "author_name": "Test",
            "word_count": 100,
            "posted_date": datetime.now(),
            "depth_score": 0.8,
            "relevance_score": 0.8,
            "originality_score": 0.8,
            "engagement_score": 0.8,
        }
        defaults.update(overrides)
        return PostAnalysis(**defaults)

    def test_base_score_without_modifiers(self, analyzer):
        """Equal scores across all dimensions with default weights."""
        analysis = self._make_analysis()
        # 0.8 * 0.4 + 0.8 * 0.3 + 0.8 * 0.2 + 0.8 * 0.1 = 0.8
        score = analyzer._calculate_rank_score(analysis)
        assert score == pytest.approx(0.8, abs=0.01)

    def test_needs_response_boost(self, analyzer):
        analysis = self._make_analysis(needs_response=True)
        score = analyzer._calculate_rank_score(analysis)
        assert score == pytest.approx(0.8 * 1.2, abs=0.01)

    def test_mentions_professor_boost(self, analyzer):
        analysis = self._make_analysis(mentions_professor=True)
        score = analyzer._calculate_rank_score(analysis)
        assert score == pytest.approx(0.8 * 1.15, abs=0.01)

    def test_high_ai_risk_penalty(self, analyzer):
        analysis = self._make_analysis(ai_generated_risk=RiskLevel.HIGH)
        score = analyzer._calculate_rank_score(analysis)
        assert score == pytest.approx(0.8 * 0.7, abs=0.01)

    def test_medium_plagiarism_penalty(self, analyzer):
        analysis = self._make_analysis(plagiarism_risk=RiskLevel.MEDIUM)
        score = analyzer._calculate_rank_score(analysis)
        assert score == pytest.approx(0.8 * 0.8, abs=0.01)

    def test_inappropriate_content_penalty(self, analyzer):
        analysis = self._make_analysis(has_inappropriate_content=True)
        score = analyzer._calculate_rank_score(analysis)
        assert score == pytest.approx(0.8 * 0.5, abs=0.01)

    def test_combined_boost_and_penalty(self, analyzer):
        """Boost + penalty are both applied multiplicatively."""
        analysis = self._make_analysis(
            needs_response=True,
            ai_generated_risk=RiskLevel.MEDIUM,
        )
        score = analyzer._calculate_rank_score(analysis)
        assert score == pytest.approx(0.8 * 1.2 * 0.85, abs=0.01)


# =============================================================================
# POST ANALYZER — Summary Generation Fallback
# =============================================================================


class TestSummaryGenerationFallback:
    """Verify fallback when LLM summary call fails."""

    def test_fallback_truncates_long_content(self):
        mock_llm = _make_mock_llm()
        mock_llm.generate_sync.side_effect = Exception("LLM down")
        analyzer = PostAnalyzer(llm_client=mock_llm)

        long_content = " ".join(["word"] * 50)
        result = analyzer._generate_summary(long_content)

        # Should truncate to 20 words + "..."
        assert result.endswith("...")
        assert len(result.split()) <= 21  # 20 words + "..."


# =============================================================================
# POST ANALYZER — Batch Analysis Parsing
# =============================================================================


class TestBatchAnalysisParsing:
    """Verify ``_parse_batch_analysis_response`` handles various formats."""

    @pytest.fixture
    def analyzer(self):
        return PostAnalyzer(llm_client=_make_mock_llm())

    def test_parses_well_formed_response(self, analyzer):
        posts = [
            _make_entry(entry_id=10, user_name="Alice", word_count=300),
            _make_entry(entry_id=20, user_name="Bob", word_count=200),
        ]
        response = (
            "1\tAlice\t8\tGreat post about ethics\tNone\tnone\tShows depth\n"
            "2\tBob\t6\tBrief but relevant\tLow\tnone\tCould expand more"
        )

        analyses = analyzer._parse_batch_analysis_response(response, posts)

        assert len(analyses) == 2
        assert analyses[0].merit_score == 8
        assert analyses[1].merit_score == 6

    def test_fills_fallback_for_missing_posts(self, analyzer):
        """If the LLM only returns analysis for 1 of 2 posts, the
        second gets a fallback analysis with default scores."""
        posts = [
            _make_entry(entry_id=10, user_name="Alice"),
            _make_entry(entry_id=20, user_name="Bob"),
        ]
        response = "1\tAlice\t7\tGood post\tNone\tnone\tSolid"

        analyses = analyzer._parse_batch_analysis_response(response, posts)

        assert len(analyses) == 2
        # The fallback should have default merit score of 5
        fallback = [a for a in analyses if a.post_id == 20][0]
        assert fallback.merit_score == 5
        assert "incomplete" in fallback.reasoning.lower()

    def test_skips_malformed_lines(self, analyzer):
        posts = [_make_entry(entry_id=10)]
        response = "garbage line\n1\tAlice\t8\tGood\tNone\tnone\tGreat"

        analyses = analyzer._parse_batch_analysis_response(response, posts)

        assert len(analyses) == 1
        assert analyses[0].merit_score == 8


# =============================================================================
# POST ANALYZER — PostRanking Model
# =============================================================================


class TestPostRanking:
    """Verify ``PostRanking`` helper methods."""

    def test_get_top_posts_returns_n_items(self):
        analyses = [
            PostAnalysis(
                post_id=i,
                author_name=f"Student {i}",
                word_count=100,
                posted_date=datetime.now(),
                final_rank_score=float(i),
            )
            for i in range(5)
        ]
        ranking = PostRanking(
            discussion_id=1,
            discussion_title="Test",
            total_posts=5,
            ranked_posts=analyses,
        )

        top = ranking.get_top_posts(3)
        assert len(top) == 3

    def test_to_dict_serialises_correctly(self):
        analysis = PostAnalysis(
            post_id=1,
            author_name="Alice",
            word_count=100,
            posted_date=datetime.now(),
            flags=[AnalysisFlag.NEEDS_RESPONSE],
        )
        ranking = PostRanking(
            discussion_id=1,
            discussion_title="Test",
            total_posts=1,
            ranked_posts=[analysis],
            flagged_posts=[analysis],
        )

        data = ranking.to_dict()
        assert data["total_posts"] == 1
        assert data["flagged_posts"][0]["flags"] == ["needs_response"]


# =============================================================================
# SIMILARITY CHECKER — Text Normalisation
# =============================================================================


class TestSimilarityNormalisation:
    """Verify ``_normalize_text`` strips HTML, lowercases, removes punctuation."""

    @pytest.fixture
    def checker(self):
        return SimilarityChecker()

    def test_strips_html_and_lowercases(self, checker):
        result = checker._normalize_text("<p>Hello <b>WORLD</b>!</p>")
        assert "hello" in result
        assert "world" in result
        assert "<" not in result

    def test_removes_punctuation(self, checker):
        result = checker._normalize_text("Hello, world! How are you?")
        assert "," not in result
        assert "!" not in result
        assert "?" not in result

    def test_empty_input(self, checker):
        assert checker._normalize_text("") == ""
        assert checker._normalize_text(None) == ""


# =============================================================================
# SIMILARITY CHECKER — Similarity Calculation
# =============================================================================


class TestSimilarityCalculation:
    """Verify ``calculate_similarity`` produces expected scores."""

    @pytest.fixture
    def checker(self):
        return SimilarityChecker()

    def test_identical_posts_have_high_similarity(self, checker):
        post_a = _make_entry(
            entry_id=1, user_id=100, message="<p>This is a test post about ethics</p>"
        )
        post_b = _make_entry(
            entry_id=2, user_id=200, message="<p>This is a test post about ethics</p>"
        )

        result = checker.calculate_similarity(post_a, post_b)

        assert result.overall_similarity > 0.9

    def test_different_posts_have_low_similarity(self, checker):
        post_a = _make_entry(
            entry_id=1,
            user_id=100,
            message="<p>Cybersecurity is important for protecting digital infrastructure.</p>",
        )
        post_b = _make_entry(
            entry_id=2,
            user_id=200,
            message="<p>Cooking pasta requires boiling water and adding salt generously.</p>",
        )

        result = checker.calculate_similarity(post_a, post_b)

        assert result.overall_similarity < 0.5


# =============================================================================
# SIMILARITY CHECKER — Discussion Check
# =============================================================================


class TestCheckDiscussion:
    """Verify ``check_discussion`` flags suspicious pairs."""

    def test_flags_near_duplicate_posts(self):
        checker = SimilarityChecker(similarity_threshold=0.85)

        posts = [
            _make_entry(
                entry_id=1,
                user_id=100,
                user_name="Alice",
                message="<p>This is my complete post about the importance of ethics</p>",
            ),
            _make_entry(
                entry_id=2,
                user_id=200,
                user_name="Bob",
                message="<p>This is my complete post about the importance of ethics</p>",
            ),
        ]

        report = checker.check_discussion(posts, discussion_id=1)

        assert isinstance(report, SimilarityReport)
        assert len(report.suspicious_pairs) > 0
        assert report.suspicious_pairs[0].is_suspicious is True

    def test_skips_same_author_comparisons(self):
        checker = SimilarityChecker(similarity_threshold=0.5)

        posts = [
            _make_entry(entry_id=1, user_id=100, user_name="Alice", message="<p>Same text</p>"),
            _make_entry(entry_id=2, user_id=100, user_name="Alice", message="<p>Same text</p>"),
        ]

        report = checker.check_discussion(posts, discussion_id=1)

        # Same user_id should be skipped
        assert len(report.suspicious_pairs) == 0

    def test_marks_likely_plagiarism_above_90_percent(self):
        checker = SimilarityChecker(similarity_threshold=0.85)

        msg = (
            "<p>This is a fairly long post about cybersecurity ethics "
            "and privacy in the digital age</p>"
        )
        posts = [
            _make_entry(entry_id=1, user_id=100, message=msg),
            _make_entry(entry_id=2, user_id=200, message=msg),
        ]

        report = checker.check_discussion(posts, discussion_id=1)

        if report.suspicious_pairs:
            assert report.suspicious_pairs[0].likely_plagiarism is True


# =============================================================================
# SIMILARITY CHECKER — LCS Similarity
# =============================================================================


class TestLcsSimilarity:
    """Verify the longest common subsequence similarity calculation."""

    @pytest.fixture
    def checker(self):
        return SimilarityChecker()

    def test_identical_sequences(self, checker):
        result = checker._lcs_similarity([5, 10, 15], [5, 10, 15])
        assert result == pytest.approx(1.0)

    def test_completely_different_sequences(self, checker):
        result = checker._lcs_similarity([1, 2, 3], [100, 200, 300])
        assert result == pytest.approx(0.0)

    def test_empty_sequences(self, checker):
        assert checker._lcs_similarity([], [1, 2]) == 0.0
        assert checker._lcs_similarity([1, 2], []) == 0.0

    def test_tolerance_window(self, checker):
        """Word counts within +/-2 are considered matches."""
        result = checker._lcs_similarity([10, 20, 30], [11, 19, 32])
        assert result == pytest.approx(1.0)


# =============================================================================
# RESPONSE GENERATOR — Draft Generation
# =============================================================================


class TestResponseGeneratorDraft:
    """Verify ``generate_response`` produces a ``ResponseDraft``."""

    def test_generates_basic_response(self):
        mock_llm = _make_mock_llm("Great insights on cybersecurity ethics!")
        generator = ResponseGenerator(llm_client=mock_llm)

        post = _make_entry(
            user_name="Alice",
            message="<p>I think cybersecurity is crucial for society.</p>",
            word_count=8,
        )

        response = generator.generate_response(
            post=post,
            discussion_title="Ethics in CS",
        )

        assert isinstance(response, ResponseDraft)
        assert response.student_name == "Alice"
        assert response.final_content == "Great insights on cybersecurity ethics!"
        assert response.includes_research is False

    def test_includes_research_when_provided(self):
        mock_llm = _make_mock_llm()
        # First call returns draft, second call returns refined
        mock_llm.generate_sync.side_effect = [
            Mock(content="Initial draft."),
            Mock(content="Refined with research."),
        ]
        generator = ResponseGenerator(llm_client=mock_llm)

        post = _make_entry()
        response = generator.generate_response(
            post=post,
            discussion_title="Test",
            research_context="Recent study found...",
        )

        assert response.includes_research is True
        assert response.final_content == "Refined with research."


# =============================================================================
# RESPONSE GENERATOR — Fallback
# =============================================================================


class TestResponseGeneratorFallback:
    """Verify fallback when LLM call fails."""

    def test_returns_generic_response_on_llm_failure(self):
        mock_llm = _make_mock_llm()
        mock_llm.generate_sync.side_effect = Exception("API timeout")
        generator = ResponseGenerator(llm_client=mock_llm)

        post = _make_entry()
        response = generator.generate_response(
            post=post,
            discussion_title="Ethics Discussion",
        )

        # Should get the fallback generic response
        assert "Thank you" in response.final_content


# =============================================================================
# RESPONSE GENERATOR — Multiple Responses
# =============================================================================


class TestMultipleResponses:
    """Verify ``generate_multiple_responses`` batch generation."""

    def test_generates_multiple_up_to_limit(self):
        mock_llm = _make_mock_llm("Response content here.")
        generator = ResponseGenerator(llm_client=mock_llm)

        entries = [_make_entry(entry_id=i, user_name=f"Student {i}") for i in range(10)]
        analyses = [
            PostAnalysis(
                post_id=i,
                author_name=f"Student {i}",
                word_count=100,
                posted_date=datetime.now(),
                summary=f"Summary {i}",
            )
            for i in range(10)
        ]
        pairs = list(zip(entries, analyses, strict=False))

        responses = generator.generate_multiple_responses(
            posts_with_analyses=pairs,
            discussion_title="Test",
            limit=3,
        )

        assert len(responses) == 3


# =============================================================================
# RESPONSE GENERATOR — File I/O Helpers
# =============================================================================


class TestResponseFileIO:
    """Verify ``save_response_to_file`` and ``save_responses_to_directory``."""

    def _make_draft(self) -> ResponseDraft:
        return ResponseDraft(
            post_id=1,
            student_name="Alice Johnson",
            discussion_title="Ethics in CS",
            post_summary="A post about ethics.",
            draft_content="Draft content.",
            final_content="Final response content.",
            generated_date=datetime.now(),
            word_count=3,
        )

    def test_saves_single_response(self, tmp_path):
        draft = self._make_draft()
        output_file = str(tmp_path / "response.md")

        save_response_to_file(draft, output_file)

        content = (tmp_path / "response.md").read_text()
        assert "Ethics in CS" in content
        assert "Alice Johnson" in content

    def test_saves_multiple_responses_to_directory(self, tmp_path):
        drafts = [self._make_draft() for _ in range(3)]
        output_dir = str(tmp_path / "responses")

        paths = save_responses_to_directory(drafts, output_dir)

        assert len(paths) == 3
        for path in paths:
            assert "Alice" in path


# =============================================================================
# RESPONSE GENERATOR — Summary Extraction
# =============================================================================


class TestResponseSummaryExtraction:
    """Verify ``_generate_summary`` extractive fallback."""

    def test_short_posts_returned_as_is(self):
        generator = ResponseGenerator(llm_client=_make_mock_llm())
        post = _make_entry(message="<p>Short post.</p>")

        summary = generator._generate_summary(post)

        assert summary == "Short post."

    def test_long_posts_truncated_to_30_words(self):
        generator = ResponseGenerator(llm_client=_make_mock_llm())
        long_message = "<p>" + " ".join(["word"] * 50) + "</p>"
        post = _make_entry(message=long_message)

        summary = generator._generate_summary(post)

        assert summary.endswith("...")
        # 30 words + "..."
        assert len(summary.split()) <= 31
