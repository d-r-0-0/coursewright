#!/usr/bin/env python3
"""
Professor Response Generator
============================
Generates thoughtful professor responses to student discussion posts.

"""

import logging
import re
from datetime import datetime

from pydantic import BaseModel

from coursewright.core.canvas.models import CanvasDiscussionEntry
from coursewright.core.llm.client import LLMClient

from .post_analyzer import PostAnalysis
from .prompts import RESPONSE_DRAFT_PROMPT, RESPONSE_MARKDOWN_TEMPLATE, RESPONSE_REFINEMENT_PROMPT

logger = logging.getLogger(__name__)


class ResponseDraft(BaseModel):
    """Draft response to a student post"""

    post_id: int
    student_name: str
    discussion_title: str
    post_summary: str

    # Response content
    draft_content: str
    refined_content: str | None = None
    final_content: str

    # Metadata
    generated_date: datetime
    word_count: int
    includes_research: bool = False

    # Research context (if web search was used)
    research_notes: str | None = None

    def get_markdown(self) -> str:
        """Format response as markdown for LMS"""
        content = self.final_content

        return RESPONSE_MARKDOWN_TEMPLATE.format(
            discussion_title=self.discussion_title,
            student_name=self.student_name,
            post_summary=self.post_summary,
            response_content=content,
            response_date=self.generated_date.strftime("%Y-%m-%d"),
        )

    def get_word_count(self) -> int:
        """Calculate word count of final response"""
        return len(self.final_content.split())


class ResponseGenerator:
    """Generates professor responses to discussion posts"""

    def __init__(self, llm_client: LLMClient):
        """
        Initialize the response generator.

        Args:
            llm_client: LLM client for generating responses
        """
        self.llm_client = llm_client

    def generate_response(
        self,
        post: CanvasDiscussionEntry,
        discussion_title: str,
        post_analysis: PostAnalysis | None = None,
        research_context: str | None = None,
    ) -> ResponseDraft:
        """
        Generate a professor response to a student post.

        Args:
            post: The discussion entry to respond to
            discussion_title: Title of the discussion
            post_analysis: Optional pre-computed analysis of the post
            research_context: Optional research findings to incorporate

        Returns:
            ResponseDraft with generated response
        """
        # Get post summary (from analysis or generate)
        post_summary = post_analysis.summary if post_analysis else self._generate_summary(post)

        # Generate initial draft
        draft = self._generate_draft(
            post=post, discussion_title=discussion_title, post_summary=post_summary
        )

        # Refine with research if provided
        if research_context:
            refined = self._refine_with_research(draft, research_context)
            final_content = refined
            includes_research = True
        else:
            final_content = draft
            includes_research = False

        # Create ResponseDraft object
        response = ResponseDraft(
            post_id=post.id,
            student_name=post.user_name or "Student",
            discussion_title=discussion_title,
            post_summary=post_summary,
            draft_content=draft,
            refined_content=final_content if research_context else None,
            final_content=final_content,
            generated_date=datetime.now(),
            word_count=len(final_content.split()),
            includes_research=includes_research,
            research_notes=research_context,
        )

        return response

    def _generate_summary(self, post: CanvasDiscussionEntry) -> str:
        """Generate a brief summary of the post"""
        clean_content = self._strip_html(post.message)

        # Simple extractive summary - first ~30 words
        words = clean_content.split()
        if len(words) <= 30:
            return clean_content

        return " ".join(words[:30]) + "..."

    def _generate_draft(
        self, post: CanvasDiscussionEntry, discussion_title: str, post_summary: str
    ) -> str:
        """Generate initial draft response"""
        clean_content = self._strip_html(post.message)

        prompt = RESPONSE_DRAFT_PROMPT.format(
            discussion_title=discussion_title,
            student_name=post.user_name or "Student",
            post_summary=post_summary,
            post_content=clean_content[:2000],  # Limit content length
        )

        try:
            response = self.llm_client.generate_sync(prompt=prompt, temperature=0.7, max_tokens=500)

            return response.content.strip()

        except Exception:
            # Fallback response
            return (
                f"Thank you for your thoughtful contribution to this discussion. "
                f"Your insights on {discussion_title} show engagement with the material. "
                f"I'd encourage you to explore this topic further and consider how it relates "
                f"to other concepts we've covered in class."
            )

    def _refine_with_research(self, draft: str, research_context: str) -> str:
        """Refine draft response with research findings"""
        prompt = RESPONSE_REFINEMENT_PROMPT.format(
            draft_response=draft, research_findings=research_context
        )

        try:
            response = self.llm_client.generate_sync(prompt=prompt, temperature=0.7, max_tokens=600)

            return response.content.strip()

        except Exception:
            # Return original draft on error
            return draft

    def generate_multiple_responses(
        self, posts_with_analyses: list, discussion_title: str, limit: int = 5
    ) -> list:
        """
        Generate responses for multiple posts.

        Args:
            posts_with_analyses: List of tuples (post, analysis)
            discussion_title: Title of the discussion
            limit: Maximum number of responses to generate

        Returns:
            List of ResponseDraft objects
        """
        responses = []

        for post, analysis in posts_with_analyses[:limit]:
            try:
                response = self.generate_response(
                    post=post, discussion_title=discussion_title, post_analysis=analysis
                )
                responses.append(response)
            except Exception as e:
                # Log error but continue with other posts
                logger.warning("Error generating response for post %s: %s", post.id, e)
                continue

        return responses

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


def save_response_to_file(response: ResponseDraft, output_path: str) -> None:
    """
    Save a response draft to a markdown file.

    Args:
        response: ResponseDraft to save
        output_path: Path to save the markdown file
    """
    markdown = response.get_markdown()

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)


def save_responses_to_directory(responses: list, output_dir: str) -> list:
    """
    Save multiple responses to a directory.

    Args:
        responses: List of ResponseDraft objects
        output_dir: Directory to save markdown files

    Returns:
        List of file paths created
    """
    from pathlib import Path

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    file_paths = []

    for i, response in enumerate(responses, 1):
        # Sanitize student name for filename
        safe_name = re.sub(r"[^\w\s-]", "", response.student_name)
        safe_name = re.sub(r"[-\s]+", "_", safe_name)

        filename = f"response_{i:02d}_{safe_name}.md"
        file_path = output_path / filename

        save_response_to_file(response, str(file_path))
        file_paths.append(str(file_path))

    return file_paths
