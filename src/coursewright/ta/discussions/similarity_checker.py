#!/usr/bin/env python3
"""
Post Similarity Checker
=======================
Detects duplicate and similar posts to identify potential plagiarism.

"""

import re
from difflib import SequenceMatcher

from pydantic import BaseModel

from coursewright.core.canvas.models import CanvasDiscussionEntry
from coursewright.core.llm.client import LLMClient

from .prompts import SIMILARITY_ANALYSIS_PROMPT


class SimilarityResult(BaseModel):
    """Result of similarity comparison between two posts"""

    post_a_id: int
    post_b_id: int
    author_a: str
    author_b: str

    # Similarity metrics
    text_similarity: float  # 0.0 to 1.0
    structural_similarity: float  # 0.0 to 1.0
    overall_similarity: float  # 0.0 to 1.0

    # Assessment
    is_suspicious: bool = False
    likely_plagiarism: bool = False
    reasoning: str = ""

    # Timestamps for analysis
    post_a_date: str | None = None
    post_b_date: str | None = None


class SimilarityReport(BaseModel):
    """Report of all similarity comparisons in a discussion"""

    discussion_id: int
    total_posts_compared: int
    suspicious_pairs: list[SimilarityResult]
    threshold: float

    def get_flagged_posts(self) -> list[int]:
        """Get list of post IDs that appear in suspicious pairs"""
        post_ids = set()
        for pair in self.suspicious_pairs:
            post_ids.add(pair.post_a_id)
            post_ids.add(pair.post_b_id)
        return list(post_ids)


class SimilarityChecker:
    """Checks for similar/duplicate posts to detect plagiarism"""

    def __init__(self, llm_client: LLMClient | None = None, similarity_threshold: float = 0.85):
        """
        Initialize the similarity checker.

        Args:
            llm_client: Optional LLM client for advanced similarity analysis
            similarity_threshold: Threshold above which posts are considered suspicious (0.0-1.0)
        """
        self.llm_client = llm_client
        self.similarity_threshold = similarity_threshold

    def check_discussion(
        self, posts: list[CanvasDiscussionEntry], discussion_id: int, use_llm: bool = False
    ) -> SimilarityReport:
        """
        Check all posts in a discussion for similarities.

        Args:
            posts: List of discussion entries
            discussion_id: ID of the discussion
            use_llm: Whether to use LLM for advanced analysis

        Returns:
            SimilarityReport with suspicious pairs
        """
        suspicious_pairs = []

        # Compare each pair of posts
        for i, post_a in enumerate(posts):
            for post_b in posts[i + 1 :]:
                # Skip if same author (students can reference their own work)
                if post_a.user_id == post_b.user_id:
                    continue

                # Calculate similarity
                similarity = self.calculate_similarity(post_a, post_b)

                # If above threshold, flag as suspicious
                if similarity.overall_similarity >= self.similarity_threshold:
                    similarity.is_suspicious = True

                    # Use LLM for deeper analysis if available
                    if use_llm and self.llm_client:
                        llm_assessment = self._llm_similarity_check(post_a, post_b)
                        similarity.likely_plagiarism = llm_assessment.get(
                            "likely_plagiarism", False
                        )
                        similarity.reasoning = llm_assessment.get("reasoning", similarity.reasoning)
                    else:
                        similarity.likely_plagiarism = similarity.overall_similarity > 0.9

                    suspicious_pairs.append(similarity)

        return SimilarityReport(
            discussion_id=discussion_id,
            total_posts_compared=len(posts),
            suspicious_pairs=suspicious_pairs,
            threshold=self.similarity_threshold,
        )

    def calculate_similarity(
        self, post_a: CanvasDiscussionEntry, post_b: CanvasDiscussionEntry
    ) -> SimilarityResult:
        """
        Calculate similarity between two posts.

        Args:
            post_a: First post
            post_b: Second post

        Returns:
            SimilarityResult with metrics
        """
        # Clean and normalize text
        text_a = self._normalize_text(post_a.message)
        text_b = self._normalize_text(post_b.message)

        # Calculate text similarity using SequenceMatcher
        text_similarity = SequenceMatcher(None, text_a, text_b).ratio()

        # Calculate structural similarity (sentence patterns)
        structural_similarity = self._calculate_structural_similarity(text_a, text_b)

        # Overall similarity is weighted average
        overall_similarity = (text_similarity * 0.7) + (structural_similarity * 0.3)

        # Create result
        result = SimilarityResult(
            post_a_id=post_a.id,
            post_b_id=post_b.id,
            author_a=post_a.user_name or "Unknown",
            author_b=post_b.user_name or "Unknown",
            text_similarity=round(text_similarity, 3),
            structural_similarity=round(structural_similarity, 3),
            overall_similarity=round(overall_similarity, 3),
            post_a_date=post_a.created_at.isoformat() if post_a.created_at else None,
            post_b_date=post_b.created_at.isoformat() if post_b.created_at else None,
            reasoning=(
                f"Text similarity: {text_similarity:.1%}, "
                f"Structural similarity: {structural_similarity:.1%}"
            ),
        )

        return result

    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison"""
        if not text:
            return ""

        # Remove HTML tags
        clean = re.sub(r"<[^>]+>", "", text)

        # Convert to lowercase
        clean = clean.lower()

        # Remove extra whitespace
        clean = re.sub(r"\s+", " ", clean)

        # Remove punctuation for comparison
        clean = re.sub(r"[^\w\s]", "", clean)

        return clean.strip()

    def _calculate_structural_similarity(self, text_a: str, text_b: str) -> float:
        """Calculate structural similarity based on sentence patterns"""
        # Split into sentences
        sentences_a = re.split(r"[.!?]+", text_a)
        sentences_b = re.split(r"[.!?]+", text_b)

        # Filter out empty sentences
        sentences_a = [s.strip() for s in sentences_a if s.strip()]
        sentences_b = [s.strip() for s in sentences_b if s.strip()]

        if not sentences_a or not sentences_b:
            return 0.0

        # Compare sentence structures (word count patterns)
        pattern_a = [len(s.split()) for s in sentences_a]
        pattern_b = [len(s.split()) for s in sentences_b]

        # If patterns are very different in length, low structural similarity
        len_diff = abs(len(pattern_a) - len(pattern_b))
        if len_diff > max(len(pattern_a), len(pattern_b)) * 0.5:
            return 0.0

        # Calculate pattern similarity
        # Use dynamic programming to find longest common subsequence
        pattern_similarity = self._lcs_similarity(pattern_a, pattern_b)

        return pattern_similarity

    def _lcs_similarity(self, seq_a: list[int], seq_b: list[int]) -> float:
        """Calculate similarity based on longest common subsequence"""
        if not seq_a or not seq_b:
            return 0.0

        # Build LCS matrix
        m, n = len(seq_a), len(seq_b)
        dp = [[0] * (n + 1) for _ in range(m + 1)]

        for i in range(1, m + 1):
            for j in range(1, n + 1):
                # Allow some tolerance in word counts (±2 words)
                if abs(seq_a[i - 1] - seq_b[j - 1]) <= 2:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                else:
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

        lcs_length = dp[m][n]
        max_length = max(m, n)

        return lcs_length / max_length if max_length > 0 else 0.0

    def _llm_similarity_check(
        self, post_a: CanvasDiscussionEntry, post_b: CanvasDiscussionEntry
    ) -> dict:
        """Use LLM to perform advanced similarity analysis"""
        if not self.llm_client:
            return {"likely_plagiarism": False, "reasoning": "LLM analysis not available"}

        # Clean content
        content_a = self._strip_html(post_a.message)[:1500]
        content_b = self._strip_html(post_b.message)[:1500]

        prompt = SIMILARITY_ANALYSIS_PROMPT.format(
            author_a=post_a.user_name or "Unknown",
            content_a=content_a,
            author_b=post_b.user_name or "Unknown",
            content_b=content_b,
        )

        try:
            response = self.llm_client.generate_sync(prompt=prompt, temperature=0.3, max_tokens=300)

            # Parse response
            likely_plagiarism = bool(
                re.search(r"LIKELY_PLAGIARISM:\s*Yes", response.content, re.IGNORECASE)
            )

            explanation_match = re.search(
                r"EXPLANATION:\s*(.+?)(?:\n|$)", response.content, re.DOTALL
            )
            explanation = explanation_match.group(1).strip() if explanation_match else ""

            return {"likely_plagiarism": likely_plagiarism, "reasoning": explanation}

        except Exception as e:
            return {"likely_plagiarism": False, "reasoning": f"LLM analysis failed: {str(e)}"}

    @staticmethod
    def _strip_html(html_text: str) -> str:
        """Remove HTML tags from text"""
        if not html_text:
            return ""

        clean = re.sub(r"<[^>]+>", "", html_text)
        clean = clean.replace("&nbsp;", " ")
        clean = clean.replace("&amp;", "&")
        clean = re.sub(r"\s+", " ", clean)

        return clean.strip()
