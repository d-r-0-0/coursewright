#!/usr/bin/env python3
"""
Discussion Post Analyzer
========================
Analyzes and ranks discussion posts using LLM-powered insights.

"""

import re
from collections.abc import Callable
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from coursewright.core.canvas.models import CanvasDiscussionEntry
from coursewright.core.llm.client import LLMClient

from .prompts import POST_ANALYSIS_PROMPT, POST_SUMMARY_PROMPT


class RiskLevel(str, Enum):  # noqa: UP042 - values compare equal to plain strings
    """Risk level indicators"""

    NONE = "None"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class AnalysisFlag(str, Enum):  # noqa: UP042 - values compare equal to plain strings
    """Flags for posts requiring attention"""

    AI_GENERATED = "ai_generated"
    PLAGIARISM = "plagiarism"
    INAPPROPRIATE = "inappropriate"
    MENTIONS_PROFESSOR = "mentions_professor"
    NEEDS_RESPONSE = "needs_response"
    HIGH_SIMILARITY = "high_similarity"


class PostAnalysis(BaseModel):
    """Analysis results for a single post"""

    post_id: int
    author_name: str
    word_count: int
    posted_date: datetime | None

    # Summary
    summary: str = ""

    # Scoring
    merit_score: int = 0  # 1-10
    depth_score: float = 0.0
    relevance_score: float = 0.0
    originality_score: float = 0.0
    engagement_score: float = 0.0

    # Risk assessment
    ai_generated_risk: RiskLevel = RiskLevel.NONE
    plagiarism_risk: RiskLevel = RiskLevel.NONE
    has_inappropriate_content: bool = False

    # Flags
    mentions_professor: bool = False
    needs_response: bool = False
    flags: list[AnalysisFlag] = Field(default_factory=list)

    # Analysis details
    reasoning: str = ""
    analysis_timestamp: datetime = Field(default_factory=datetime.now)

    # Ranking
    final_rank_score: float = 0.0
    rank_position: int | None = None


class PostRanking(BaseModel):
    """Ranked list of post analyses"""

    discussion_id: int
    discussion_title: str
    total_posts: int
    analysis_date: datetime = Field(default_factory=datetime.now)

    ranked_posts: list[PostAnalysis] = Field(default_factory=list)
    flagged_posts: list[PostAnalysis] = Field(default_factory=list)

    def get_top_posts(self, n: int = 10) -> list[PostAnalysis]:
        """Get the top N ranked posts"""
        return self.ranked_posts[:n]

    def get_flagged_posts(self) -> list[PostAnalysis]:
        """Get all posts with flags"""
        return self.flagged_posts

    def to_dict(self) -> dict:
        """Export to dictionary for JSON serialization"""
        return {
            "discussion_id": self.discussion_id,
            "discussion_title": self.discussion_title,
            "total_posts": self.total_posts,
            "analysis_date": self.analysis_date.isoformat(),
            "ranked_posts": [
                {
                    "rank": i + 1,
                    "post_id": post.post_id,
                    "author": post.author_name,
                    "summary": post.summary,
                    "merit_score": post.merit_score,
                    "flags": [f.value for f in post.flags],
                }
                for i, post in enumerate(self.ranked_posts)
            ],
            "flagged_posts": [
                {
                    "post_id": post.post_id,
                    "author": post.author_name,
                    "flags": [f.value for f in post.flags],
                    "reasoning": post.reasoning,
                }
                for post in self.flagged_posts
            ],
        }


class PostAnalyzer:
    """Analyzes and ranks discussion posts using LLM"""

    def __init__(self, llm_client: LLMClient, ranking_weights: dict[str, float] | None = None):
        """
        Initialize the post analyzer.

        Args:
            llm_client: LLM client for analysis
            ranking_weights: Custom weights for ranking (depth, relevance, originality, engagement)
        """
        self.llm_client = llm_client

        # Default ranking weights
        self.ranking_weights = ranking_weights or {
            "depth": 0.4,
            "relevance": 0.3,
            "originality": 0.2,
            "engagement": 0.1,
        }

    def analyze_post(self, post: CanvasDiscussionEntry, discussion_title: str) -> PostAnalysis:
        """
        Analyze a single discussion post.

        Args:
            post: The discussion entry to analyze
            discussion_title: Title of the discussion topic

        Returns:
            PostAnalysis with scores and flags
        """
        # Strip HTML from post content
        clean_content = self._strip_html(post.message)

        # Generate summary
        summary = self._generate_summary(clean_content)

        # Perform detailed analysis
        analysis_result = self._analyze_content(
            clean_content,
            discussion_title,
            post.user_name or "Unknown",
            post.created_at or datetime.now(),
            post.word_count,
        )

        # Create PostAnalysis object
        post_analysis = PostAnalysis(
            post_id=post.id,
            author_name=post.user_name or "Unknown",
            word_count=post.word_count,
            posted_date=post.created_at,
            summary=summary,
            **analysis_result,
        )

        # Calculate final ranking score
        post_analysis.final_rank_score = self._calculate_rank_score(post_analysis)

        return post_analysis

    def analyze_discussion(
        self,
        posts: list[CanvasDiscussionEntry],
        discussion_id: int,
        discussion_title: str,
        progress_callback: Callable | None = None,
    ) -> PostRanking:
        """
        Analyze all posts in a discussion and rank them.

        Args:
            posts: List of discussion entries
            discussion_id: ID of the discussion
            discussion_title: Title of the discussion
            progress_callback: Optional callback for progress updates

        Returns:
            PostRanking with ranked and flagged posts
        """
        analyses: list[PostAnalysis] = []

        # Analyze each post
        for i, post in enumerate(posts):
            analysis = self.analyze_post(post, discussion_title)
            analyses.append(analysis)

            if progress_callback:
                progress_callback(i + 1, len(posts))

        # Sort by ranking score (highest first)
        ranked_posts = sorted(analyses, key=lambda x: x.final_rank_score, reverse=True)

        # Assign rank positions
        for i, post in enumerate(ranked_posts):
            post.rank_position = i + 1

        # Identify flagged posts
        flagged_posts = [post for post in analyses if len(post.flags) > 0]

        return PostRanking(
            discussion_id=discussion_id,
            discussion_title=discussion_title,
            total_posts=len(posts),
            ranked_posts=ranked_posts,
            flagged_posts=flagged_posts,
        )

    def analyze_discussion_batch(
        self,
        posts: list[CanvasDiscussionEntry],
        discussion_id: int,
        discussion_title: str,
        progress_callback: Callable | None = None,
    ) -> PostRanking:
        """
        Analyze all posts in a single batch LLM request (much faster than individual calls).

        Args:
            posts: List of discussion entries
            discussion_id: ID of the discussion
            discussion_title: Title of the discussion
            progress_callback: Optional callback for progress updates

        Returns:
            PostRanking with ranked and flagged posts
        """
        try:
            # Build batch prompt with all posts
            batch_prompt = self._create_batch_analysis_prompt(posts, discussion_title)

            if progress_callback:
                progress_callback(0.3, 1.0, "Sending batch analysis request...")

            # Single LLM call for all posts
            response = self.llm_client.generate_sync(
                prompt=batch_prompt,
                max_tokens=3000,  # Need more tokens for batch analysis
            )

            if progress_callback:
                progress_callback(0.7, 1.0, "Parsing batch response...")

            # Parse the batch response into individual analyses
            analyses = self._parse_batch_analysis_response(response.content, posts)

            if progress_callback:
                progress_callback(1.0, 1.0, "Complete!")

        except Exception as e:
            # Fallback to individual analysis if batch fails
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Batch analysis failed, falling back to individual analysis: {e}")
            return self.analyze_discussion(
                posts, discussion_id, discussion_title, progress_callback
            )

        # Sort by ranking score (highest first)
        ranked_posts = sorted(analyses, key=lambda x: x.final_rank_score, reverse=True)

        # Assign rank positions
        for i, post in enumerate(ranked_posts):
            post.rank_position = i + 1

        # Identify flagged posts
        flagged_posts = [post for post in analyses if post.flags]

        return PostRanking(
            discussion_id=discussion_id,
            discussion_title=discussion_title,
            total_posts=len(posts),
            ranked_posts=ranked_posts,
            flagged_posts=flagged_posts,
        )

    def _create_batch_analysis_prompt(
        self, posts: list[CanvasDiscussionEntry], discussion_title: str
    ) -> str:
        """Create a batch analysis prompt for all posts"""

        prompt_parts = [
            "You are the instructor's teaching assistant, analyzing multiple student "
            "discussion posts in a single batch.",
            "",
            f"**Discussion Topic:** {discussion_title}",
            f"**Total Posts to Analyze:** {len(posts)}",
            "",
            "For EACH post below, provide analysis in this EXACT tab-separated format:",
            "POST_ID\\tAUTHOR\\tMERIT\\tSUMMARY\\tAI_RISK\\tFLAGS\\tREASONING",
            "",
            "Where:",
            "- POST_ID: The post number (1, 2, 3, etc.)",
            "- AUTHOR: Author name",
            "- MERIT: Score from 1-10",
            "- SUMMARY: One sentence (under 25 words)",
            "- AI_RISK: None/Low/Medium/High",
            "- FLAGS: Comma-separated list (or 'none' if no flags)",
            "- REASONING: Brief explanation (1-2 sentences)",
            "",
            "**POSTS TO ANALYZE:**",
            "=" * 80,
            "",
        ]

        # Add each post
        for i, post in enumerate(posts, 1):
            clean_content = self._strip_html(post.message)
            # Truncate long posts
            content_preview = (
                clean_content[:500] + "..." if len(clean_content) > 500 else clean_content
            )

            prompt_parts.extend(
                [
                    f"**POST #{i}**",
                    f"Author: {post.user_name or 'Unknown'}",
                    "Posted: "
                    + (post.created_at.strftime("%Y-%m-%d %H:%M") if post.created_at else "N/A"),
                    f"Words: {post.word_count}",
                    f"Content: {content_preview}",
                    "",
                ]
            )

        prompt_parts.extend(
            ["=" * 80, "", "**YOUR ANALYSIS (one line per post in the format specified above):**"]
        )

        return "\n".join(prompt_parts)

    def _parse_batch_analysis_response(
        self, response_text: str, posts: list[CanvasDiscussionEntry]
    ) -> list[PostAnalysis]:
        """Parse batch response into PostAnalysis objects"""

        analyses = []
        lines = response_text.strip().split("\n")

        for line in lines:
            if not line.strip() or "\t" not in line:
                continue

            try:
                parts = line.split("\t")
                if len(parts) < 7:
                    continue

                post_num = int(parts[0].strip())
                if post_num < 1 or post_num > len(posts):
                    continue

                post = posts[post_num - 1]
                author = parts[1].strip()
                merit = int(parts[2].strip())
                summary = parts[3].strip()
                ai_risk = parts[4].strip()
                flags_str = parts[5].strip()
                reasoning = parts[6].strip()

                # Parse flags
                flags = []
                if flags_str.lower() not in ["none", "-", ""]:
                    flag_names = [f.strip().lower() for f in flags_str.split(",")]
                    for flag_name in flag_names:
                        if "ai" in flag_name or "generated" in flag_name:
                            flags.append(AnalysisFlag.AI_GENERATED)
                        elif "professor" in flag_name or "mention" in flag_name:
                            flags.append(AnalysisFlag.MENTIONS_PROFESSOR)
                        elif "response" in flag_name or "question" in flag_name:
                            flags.append(AnalysisFlag.NEEDS_RESPONSE)
                        elif "plagiarism" in flag_name or "similar" in flag_name:
                            flags.append(AnalysisFlag.PLAGIARISM)

                # Map risk level
                risk_map = {
                    "none": RiskLevel.NONE,
                    "low": RiskLevel.LOW,
                    "medium": RiskLevel.MEDIUM,
                    "high": RiskLevel.HIGH,
                }
                ai_risk_level = risk_map.get(ai_risk.lower(), RiskLevel.NONE)

                # Create analysis
                analysis = PostAnalysis(
                    post_id=post.id,
                    author_name=author,
                    word_count=post.word_count,
                    posted_date=post.created_at,
                    summary=summary,
                    merit_score=merit,
                    depth_score=merit / 10.0,  # Normalize to 0-1
                    relevance_score=merit / 10.0,
                    originality_score=merit / 10.0,
                    engagement_score=merit / 10.0,
                    ai_generated_risk=ai_risk_level,
                    flags=flags,
                    reasoning=reasoning,
                )

                # Calculate final rank score
                analysis.final_rank_score = self._calculate_rank_score(analysis)

                analyses.append(analysis)

            except Exception:
                # Skip malformed lines
                continue

        # If we didn't get enough analyses, fill in with fallbacks
        if len(analyses) < len(posts):
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Batch parsing incomplete: got {len(analyses)}/{len(posts)} analyses")

            analyzed_ids = {a.post_id for a in analyses}
            for post in posts:
                if post.id not in analyzed_ids:
                    # Create minimal fallback analysis
                    clean_content = self._strip_html(post.message)
                    analyses.append(
                        PostAnalysis(
                            post_id=post.id,
                            author_name=post.user_name or "Unknown",
                            word_count=post.word_count,
                            posted_date=post.created_at,
                            summary=clean_content[:100] + "..."
                            if len(clean_content) > 100
                            else clean_content,
                            merit_score=5,
                            depth_score=0.5,
                            relevance_score=0.5,
                            originality_score=0.5,
                            engagement_score=0.5,
                            final_rank_score=0.5,
                            reasoning="Analysis incomplete",
                        )
                    )

        return analyses

    def _generate_summary(self, content: str) -> str:
        """Generate a one-sentence summary of the post"""
        prompt = POST_SUMMARY_PROMPT.format(post_content=content[:2000])

        try:
            response = self.llm_client.generate_sync(prompt=prompt, temperature=0.3, max_tokens=100)
            return response.content.strip()
        except Exception:
            # Fallback to simple truncation
            words = content.split()
            return " ".join(words[:20]) + "..." if len(words) > 20 else content

    def _analyze_content(
        self,
        content: str,
        discussion_title: str,
        author_name: str,
        posted_date: datetime,
        word_count: int,
    ) -> dict:
        """Perform detailed analysis of post content"""
        prompt = POST_ANALYSIS_PROMPT.format(
            discussion_title=discussion_title,
            author_name=author_name,
            posted_date=posted_date.strftime("%Y-%m-%d %H:%M") if posted_date else "Unknown",
            word_count=word_count,
            post_content=content[:3000],  # Limit content length
        )

        try:
            response = self.llm_client.generate_sync(prompt=prompt, temperature=0.3, max_tokens=500)

            # Parse the structured response
            return self._parse_analysis_response(response.content)

        except Exception as e:
            # Return default values on error
            return {
                "merit_score": 5,
                "depth_score": 0.5,
                "relevance_score": 0.5,
                "originality_score": 0.5,
                "engagement_score": 0.5,
                "ai_generated_risk": RiskLevel.NONE,
                "plagiarism_risk": RiskLevel.NONE,
                "has_inappropriate_content": False,
                "mentions_professor": False,
                "needs_response": False,
                "flags": [],
                "reasoning": f"Analysis error: {str(e)}",
            }

    def _parse_analysis_response(self, response_text: str) -> dict:
        """Parse the structured analysis response from LLM"""
        result = {
            "merit_score": 5,
            "depth_score": 0.5,
            "relevance_score": 0.5,
            "originality_score": 0.5,
            "engagement_score": 0.5,
            "ai_generated_risk": RiskLevel.NONE,
            "plagiarism_risk": RiskLevel.NONE,
            "has_inappropriate_content": False,
            "mentions_professor": False,
            "needs_response": False,
            "flags": [],
            "reasoning": "",
        }

        # Extract merit score
        merit_match = re.search(r"MERIT_SCORE:\s*(\d+)", response_text)
        if merit_match:
            score = int(merit_match.group(1))
            result["merit_score"] = max(1, min(10, score))
            # Convert to normalized scores
            result["depth_score"] = score / 10.0
            result["relevance_score"] = score / 10.0
            result["originality_score"] = score / 10.0
            result["engagement_score"] = score / 10.0

        # Extract risk assessments
        ai_risk_match = re.search(r"AI_GENERATED_RISK:\s*(\w+)", response_text)
        if ai_risk_match:
            risk = ai_risk_match.group(1)
            result["ai_generated_risk"] = (
                RiskLevel(risk) if risk in RiskLevel.__members__.values() else RiskLevel.NONE
            )
            if result["ai_generated_risk"] in [RiskLevel.MEDIUM, RiskLevel.HIGH]:
                result["flags"].append(AnalysisFlag.AI_GENERATED)

        plagiarism_match = re.search(r"PLAGIARISM_RISK:\s*(\w+)", response_text)
        if plagiarism_match:
            risk = plagiarism_match.group(1)
            result["plagiarism_risk"] = (
                RiskLevel(risk) if risk in RiskLevel.__members__.values() else RiskLevel.NONE
            )
            if result["plagiarism_risk"] in [RiskLevel.MEDIUM, RiskLevel.HIGH]:
                result["flags"].append(AnalysisFlag.PLAGIARISM)

        # Extract boolean flags
        if re.search(r"INAPPROPRIATE_CONTENT:\s*Yes", response_text, re.IGNORECASE):
            result["has_inappropriate_content"] = True
            result["flags"].append(AnalysisFlag.INAPPROPRIATE)

        if re.search(r"MENTIONS_PROFESSOR:\s*Yes", response_text, re.IGNORECASE):
            result["mentions_professor"] = True
            result["flags"].append(AnalysisFlag.MENTIONS_PROFESSOR)

        if re.search(r"NEEDS_RESPONSE:\s*Yes", response_text, re.IGNORECASE):
            result["needs_response"] = True
            result["flags"].append(AnalysisFlag.NEEDS_RESPONSE)

        # Extract reasoning
        reasoning_match = re.search(r"BRIEF_REASONING:\s*(.+?)(?:\n|$)", response_text, re.DOTALL)
        if reasoning_match:
            result["reasoning"] = reasoning_match.group(1).strip()

        return result

    def _calculate_rank_score(self, analysis: PostAnalysis) -> float:
        """Calculate final ranking score based on weights"""
        score = (
            analysis.depth_score * self.ranking_weights["depth"]
            + analysis.relevance_score * self.ranking_weights["relevance"]
            + analysis.originality_score * self.ranking_weights["originality"]
            + analysis.engagement_score * self.ranking_weights["engagement"]
        )

        # Boost posts that need response or mention professor
        if analysis.needs_response:
            score *= 1.2
        if analysis.mentions_professor:
            score *= 1.15

        # Penalize posts with risk flags
        if analysis.ai_generated_risk == RiskLevel.HIGH:
            score *= 0.7
        elif analysis.ai_generated_risk == RiskLevel.MEDIUM:
            score *= 0.85

        if analysis.plagiarism_risk == RiskLevel.HIGH:
            score *= 0.6
        elif analysis.plagiarism_risk == RiskLevel.MEDIUM:
            score *= 0.8

        if analysis.has_inappropriate_content:
            score *= 0.5

        return round(score, 3)

    @staticmethod
    def _strip_html(html_text: str) -> str:
        """Remove HTML tags from text"""
        if not html_text:
            return ""

        # Remove HTML tags
        clean = re.sub(r"<[^>]+>", "", html_text)

        # Convert common HTML entities
        clean = clean.replace("&nbsp;", " ")
        clean = clean.replace("&amp;", "&")
        clean = clean.replace("&lt;", "<")
        clean = clean.replace("&gt;", ">")
        clean = clean.replace("&quot;", '"')

        # Clean up whitespace
        clean = re.sub(r"\s+", " ", clean)

        return clean.strip()
