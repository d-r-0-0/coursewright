"""
LLM Feedback Generator
=====================
Generate personalized, encouraging feedback for students using LLM APIs.
"""

# Prompt text is kept verbatim from the source (rule 3.5: same output), so
# its long lines are not wrapped.
# ruff: noqa: E501

from coursewright.core.canvas.models import DiscussionAnalysis, StudentParticipation
from coursewright.core.llm.client import LLMClient, LLMProvider
from coursewright.core.llm.prompt_library import load_prompt
from coursewright.ta.grading import ai_scoring


def _load_bundled_prompt(prompt_file_name: str) -> str | None:
    """Return a bundled prompt's text, or None so callers use their fallback."""
    try:
        return load_prompt(prompt_file_name)
    except FileNotFoundError:
        return None


def _coerce_post_number(value) -> int | None:
    """Best-effort conversion of an LLM-returned post number to int|None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {"null", "none", "n/a"}:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


class FeedbackGenerator:
    """Generate personalized student feedback using LLM"""

    def __init__(self, llm_client: LLMClient, verbose: bool = False):
        """
        Initialize feedback generator.

        Args:
            llm_client: Configured LLM client instance
            verbose: If True, show detailed LLM communications
        """
        self.llm_client = llm_client
        self.verbose = verbose
        # Errors accumulated during generation — callers can inspect to surface failures.
        self.errors: list[str] = []

    def generate_student_feedback(
        self,
        student: StudentParticipation,
        discussion_context: str,
        rubric_info: dict[str, float],
        use_fallback_on_error: bool = True,
    ) -> str:
        """
        Generate encouraging, personalized feedback for a student.

        Args:
            student: Student participation data
            discussion_context: Brief context about the discussion topic
            rubric_info: Grading rubric information (points breakdown)
            use_fallback_on_error: If True, use fallback feedback when LLM fails

        Returns:
            Personalized feedback text
        """
        try:
            system_prompt = self._create_system_prompt()
            user_prompt = self._create_student_prompt(student, discussion_context, rubric_info)

            response = self.llm_client.generate_sync(
                prompt=user_prompt, system_prompt=system_prompt, temperature=0.7, max_tokens=500
            )

            return response.content.strip()

        except Exception as e:
            if use_fallback_on_error:
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(f"LLM API call failed, using fallback feedback: {e}")
                # Record the error so callers can surface it to the user.
                self.errors.append(str(e))
                return self._create_fallback_feedback(student)
            else:
                # Re-raise if caller wants to handle errors themselves
                raise

    def generate_bulk_feedback(
        self,
        analysis: DiscussionAnalysis,
        discussion_context: str,
        rubric_info: dict[str, float],
        max_students: int | None = None,
    ) -> dict[int, str]:
        """
        Generate feedback for multiple students in bulk.

        Args:
            analysis: Complete discussion analysis with all students
            discussion_context: Brief context about the discussion topic
            rubric_info: Grading rubric information
            max_students: Optional limit on number of students to process

        Returns:
            Dictionary mapping student user_id to feedback text
        """
        feedback_map = {}
        students = analysis.student_participation

        if max_students:
            students = students[:max_students]

        for student in students:
            try:
                feedback = self.generate_student_feedback(student, discussion_context, rubric_info)
                feedback_map[student.user_id] = feedback
            except Exception as bulk_err:
                # If LLM call fails, provide a fallback message
                import logging

                logging.getLogger(__name__).warning(
                    f"LLM call failed for {student.user_name}: {bulk_err}"
                )
                self.errors.append(str(bulk_err))
                feedback_map[student.user_id] = self._create_fallback_feedback(student)

        return feedback_map

    def verify_role_assignment(
        self,
        student: StudentParticipation,
    ) -> dict | None:
        """Ask the LLM to double-check the AI discussion role assignment.

        The assignment template designates three posts:

        * **Research** — the initial top-level post answering the prompt.
        * **Restatement** — a reply to the student's own research post that
          restates / extends it (~300 words).
        * **Reply** — a reply to a classmate's research post (~150 words).

        Our structural classifier handles tidy submissions, but when
        students post out of order or thread their replies oddly we fall
        back to a brute-force optimizer. This method gives the LLM a
        second look so we can correct mis-mappings before finalizing the
        grade.

        Returns a dict like::

            {
                "research": <post_number or None>,
                "restatement": <post_number or None>,
                "reply": <post_number or None>,
                "rationale": "<one short sentence>",
            }

        or ``None`` if the call or parse fails (caller should keep the
        current assignment in that case).
        """
        if not student.post_details:
            return None
        try:
            system_prompt = self._create_role_check_system_prompt()
            user_prompt = self._create_role_check_user_prompt(student)
            response = self.llm_client.generate_sync(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.0,
                max_tokens=200,
            )
            return self._parse_role_check_response(response.content)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                f"LLM role verification failed for {student.user_name}: {exc}"
            )
            self.errors.append(f"role-verify: {exc}")
            return None

    def _create_role_check_system_prompt(self) -> str:
        """System prompt instructing the LLM to return JSON role mappings."""
        return (
            "You are auditing role assignments for an AI / cybersecurity "
            "discussion forum. The assignment requires three posts per "
            "student:\n"
            "  - Research (Post 1): the student's initial top-level post.\n"
            "  - Restatement (Post 2): the student's reply to their OWN "
            "research post, restating / extending it (~300 words).\n"
            "  - Reply (Post 3): a reply to a CLASSMATE's research post "
            "(~150 words).\n\n"
            "Given a numbered list of one student's posts (with parent "
            "relationship, word count, and a content snippet), decide which "
            "post number best fills each role. A post can only fill ONE "
            "role. Use null when no post fits a role.\n\n"
            "Respond with ONLY a single JSON object, no prose, no code "
            "fences, in exactly this shape:\n"
            '{"research": <post_number|null>, '
            '"restatement": <post_number|null>, '
            '"reply": <post_number|null>, '
            '"rationale": "<one short sentence>"}'
        )

    def _create_role_check_user_prompt(self, student: StudentParticipation) -> str:
        """Render the student's posts for LLM role inspection."""
        lines: list[str] = [
            f"Student: {student.user_name}",
            f"Total posts: {len(student.post_details)}",
            "",
            "Posts:",
        ]
        for detail in student.post_details:
            post_number = detail.get("post_number", "?")
            word_count = detail.get("word_count", 0)
            parent_relationship = detail.get("parent_relationship", "unknown")
            current_role = detail.get("post_role", "extra")
            snippet = (detail.get("text_snippet") or "").strip()
            if len(snippet) > 400:
                snippet = snippet[:400] + "..."
            lines.append(
                f"- Post #{post_number} | {word_count} words | "
                f"parent: {parent_relationship} | current role: {current_role}"
            )
            lines.append(f"  Snippet: {snippet}")
        lines.append("")
        lines.append("Return the JSON role mapping now.")
        return "\n".join(lines)

    def _parse_role_check_response(self, content: str) -> dict | None:
        """Extract the first JSON object from the LLM's response."""
        import json
        import re

        if not content:
            return None
        # Strip code fences if the model added them despite instructions.
        cleaned = content.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        result: dict = {
            "research": _coerce_post_number(parsed.get("research")),
            "restatement": _coerce_post_number(parsed.get("restatement")),
            "reply": _coerce_post_number(parsed.get("reply")),
            "rationale": str(parsed.get("rationale", "")).strip(),
        }
        # If every role is null, treat as no useful answer.
        if all(result[k] is None for k in ("research", "restatement", "reply")):
            return None
        return result

    def _create_system_prompt(self) -> str:
        """Create the system prompt for the LLM"""
        bundled_prompt = _load_bundled_prompt("FEEDBACK_SYSTEM.md")
        if bundled_prompt is not None:
            return bundled_prompt

        # Fallback to hardcoded prompt
        return """You are a supportive college instructor providing feedback on student discussion forum posts.

Your feedback should be:
- Encouraging and positive in tone
- Brief (2-3 sentences, 25-50 words maximum)
- Specific to the student's participation
- Constructive when pointing out areas for improvement
- Professional and warm

When a student did well, highlight their strengths.
When deductions occurred, gently explain what was missing and encourage them for next time.

Focus on helping students learn and improve, not just on point deductions."""

    def _create_student_prompt(
        self, student: StudentParticipation, discussion_context: str, rubric_info: dict[str, float]
    ) -> str:
        """Create the user prompt with student data.

        The discussion may be graded under either the legacy rubric
        (one substantial post + replies) or the AI Discussion rubric
        (Research 30 / Restatement 40 / Reply 30).  We describe to the
        LLM whichever rubric actually produced the grade so its feedback
        lines up with the points the student earned.
        """
        if ai_scoring.is_ai_discussion_rubric(getattr(student, "post_details", None), rubric_info):
            return self._create_ai_student_prompt(student, discussion_context, rubric_info)
        return self._create_legacy_student_prompt(student, discussion_context, rubric_info)

    @staticmethod
    def _has_ai_roles(student: StudentParticipation) -> bool:
        """Detect whether the student was graded under the AI rubric.

        The ``ai_discussion`` grading profile tags posts with explicit
        ``research`` / ``restatement`` / ``reply`` roles; the legacy
        profile tags every post ``"extra"``.  The presence of any real
        role is our signal to describe the AI rubric to the LLM.
        """
        post_details = getattr(student, "post_details", None) or []
        return ai_scoring.has_explicit_ai_roles(post_details)

    def _create_ai_student_prompt(
        self, student: StudentParticipation, discussion_context: str, rubric_info: dict[str, float]
    ) -> str:
        """Build the AI-Discussion-rubric-aware feedback prompt."""
        research_points = rubric_info.get("research_points", ai_scoring.RESEARCH_POINTS)
        restatement_points = rubric_info.get("restatement_points", ai_scoring.RESTATEMENT_POINTS)
        reply_points = rubric_info.get("reply_points", ai_scoring.REPLY_POINTS)
        research_min_words = rubric_info.get("min_word_count", 300)

        # Suss out the three role word counts (explicit labels preferred,
        # structural relationship as a fallback) and the points each earned.
        role_word_counts = ai_scoring.roles_from_post_details(student.post_details)
        research_earned = ai_scoring.score_role(
            role_word_counts["research"], research_min_words, research_points
        )
        restatement_earned = ai_scoring.score_role(
            role_word_counts["restatement"], ai_scoring.RESTATEMENT_MIN_WORDS, restatement_points
        )
        reply_earned = ai_scoring.score_role(
            role_word_counts["reply"], ai_scoring.REPLY_MIN_WORDS, reply_points
        )

        missing_text = ""
        if student.missing_requirements:
            missing_text = f"- Missing: {', '.join(student.missing_requirements)}"

        post_content = self._build_post_content(student)

        template = _load_bundled_prompt("FEEDBACK_USER_AI.md")
        if template is not None:
            return template.format(
                discussion_context=discussion_context,
                student_name=student.user_name,
                total_score=f"{student.total_score:.1f}",
                research_words=role_word_counts["research"],
                research_earned=f"{research_earned:.1f}",
                research_points=f"{research_points:.0f}",
                restatement_words=role_word_counts["restatement"],
                restatement_earned=f"{restatement_earned:.1f}",
                restatement_points=f"{restatement_points:.0f}",
                reply_words=role_word_counts["reply"],
                reply_earned=f"{reply_earned:.1f}",
                reply_points=f"{reply_points:.0f}",
                meets_requirements="Yes" if student.meets_requirements else "No",
                missing_requirements=missing_text,
                post_content=post_content,
            )

        # Fallback to an inline AI-rubric prompt when the template is missing.
        prompt = f"""Generate brief, encouraging feedback for this student's AI Discussion participation.

Discussion Topic: {discussion_context}

AI Discussion rubric (100 points total):
- Post 1 Research (30 pts): deep-research prompts + full, unedited AI output
- Post 2 Restatement (40 pts): 300+ words restating the AI output in the student's own words
- Post 3 Reply (30 pts): 150+ word substantive reply to a classmate

Student Performance:
- Name: {student.user_name}
- Total Score: {student.total_score:.1f}/100
- Post 1 (Research): {role_word_counts["research"]} words (earned {research_earned:.1f}/{research_points:.0f})
- Post 2 (Restatement): {role_word_counts["restatement"]} words (earned {restatement_earned:.1f}/{restatement_points:.0f})
- Post 3 (Reply): {role_word_counts["reply"]} words (earned {reply_earned:.1f}/{reply_points:.0f})
- Meets Requirements: {"Yes" if student.meets_requirements else "No"}
"""
        if student.missing_requirements:
            prompt += f"- Missing: {', '.join(student.missing_requirements)}\n"
        if post_content and post_content != "(no post text available)":
            prompt += "\n**What the student wrote:**\n" + post_content + "\n"
        prompt += (
            "\nWrite 1-2 sentences (30-50 words) grounded in the rubric above. "
            "Name what lost points plainly; acknowledge what genuinely landed. "
            "No bullet points, no score breakdown."
        )
        return prompt

    @staticmethod
    def _build_post_content(student: StudentParticipation) -> str:
        """Render the student's post snippets for inclusion in a prompt."""
        post_content_lines = []
        if student.post_details:
            for detail in student.post_details:
                snippet = detail.get("text_snippet", "")
                if not snippet:
                    continue
                label = "Main post" if detail.get("is_initial") else "Reply"
                wc = detail.get("word_count", 0)
                post_content_lines.append(f"[{label} – {wc} words]: {snippet}")
        return "\n".join(post_content_lines) if post_content_lines else "(no post text available)"

    def _create_legacy_student_prompt(
        self, student: StudentParticipation, discussion_context: str, rubric_info: dict[str, float]
    ) -> str:
        """Create the legacy (substantial-post + replies) feedback prompt."""
        # Extract rubric information
        substantial_points = rubric_info.get("substantial_post_points", 60)
        reply_points = rubric_info.get("reply_points_each", 20)
        max_replies = rubric_info.get("max_replies_counted", 2)
        min_words = rubric_info.get("min_word_count", 300)

        # Calculate what student earned
        substantial_earned = min(
            (student.initial_post_word_count / min_words) * substantial_points, substantial_points
        )
        reply_earned = min(student.reply_count, max_replies) * reply_points

        # Build missing requirements string
        missing_text = ""
        if student.missing_requirements:
            missing_text = f"- Missing: {', '.join(student.missing_requirements)}"

        # Build post content section from stored text snippets
        post_content = self._build_post_content(student)

        # Load template from file
        template = _load_bundled_prompt("FEEDBACK_USER.md")
        if template is not None:
            return template.format(
                discussion_context=discussion_context,
                student_name=student.user_name,
                total_score=f"{student.total_score:.1f}",
                word_count=student.initial_post_word_count,
                substantial_earned=f"{substantial_earned:.1f}",
                substantial_points=substantial_points,
                reply_count=student.reply_count,
                reply_earned=f"{reply_earned:.1f}",
                reply_max_points=max_replies * reply_points,
                meets_requirements="Yes" if student.meets_requirements else "No",
                missing_requirements=missing_text,
                post_content=post_content,
            )

        # Fallback to hardcoded prompt
        prompt = f"""Generate brief, encouraging feedback for this student's discussion participation.

Discussion Topic: {discussion_context}

Student Performance:
- Name: {student.user_name}
- Total Score: {student.total_score:.1f}/100
- Substantial Post: {student.initial_post_word_count} words (earned {substantial_earned:.1f}/{substantial_points} points)
- Replies: {student.reply_count} replies (earned {reply_earned:.1f}/{max_replies * reply_points} points)
- Meets Requirements: {"Yes" if student.meets_requirements else "No"}
"""

        if student.missing_requirements:
            prompt += f"- Missing: {', '.join(student.missing_requirements)}\n"

        # Add post content if available
        if post_content and post_content != "(no post text available)":
            prompt += "\n**What the student wrote:**\n"
            prompt += post_content + "\n"

        prompt += """
Provide 3-5 sentences of feedback that:
1. References a specific idea, argument, or example the student raised
2. Acknowledges their effort and participation
3. If they did well, specifically compliments what they did right
4. If points were deducted, gently explains why and encourages improvement
5. Ends on a positive, supportive note

Keep it warm and educational. No bullet points - write as flowing sentences."""

        return prompt

    def _create_fallback_feedback(self, student: StudentParticipation) -> str:
        """Create simple fallback feedback if LLM fails"""
        if student.meets_requirements:
            return (
                f"Great work on your discussion participation! "
                f"You met all requirements with {student.total_posts} thoughtful posts. "
                f"Your engagement with peers shows good understanding of the material."
            )
        else:
            missing = ", ".join(student.missing_requirements)
            return (
                f"Thank you for your participation. "
                f"To earn full credit next time, please make sure to: {missing}. "
                f"I appreciate your effort and look forward to seeing your continued growth!"
            )

    def generate_enhanced_comment(
        self,
        student: StudentParticipation,
        discussion_context: str,
        rubric_info: dict[str, float],
        include_score_breakdown: bool = True,
    ) -> str:
        """
        Generate a complete comment with LLM feedback and optional score breakdown.

        Args:
            student: Student participation data
            discussion_context: Discussion topic context
            rubric_info: Rubric information
            include_score_breakdown: Whether to include detailed score breakdown

        Returns:
            Complete comment text ready for Canvas gradebook
        """
        # Generate personalized feedback
        llm_feedback = self.generate_student_feedback(student, discussion_context, rubric_info)

        comment_parts = [llm_feedback]

        if include_score_breakdown:
            # Add score breakdown
            substantial_points = rubric_info.get("substantial_post_points", 60)
            reply_points = rubric_info.get("reply_points_each", 20)
            max_replies = rubric_info.get("max_replies_counted", 2)
            min_words = rubric_info.get("min_word_count", 300)

            substantial_earned = min(
                (student.initial_post_word_count / min_words) * substantial_points,
                substantial_points,
            )
            reply_earned = min(student.reply_count, max_replies) * reply_points

            breakdown = f"""
Score Breakdown:
- Substantial Post: {substantial_earned:.1f}/{substantial_points} points ({student.initial_post_word_count} words)
- Replies: {reply_earned:.1f}/{max_replies * reply_points} points ({student.reply_count} replies)
- Total: {student.total_score:.1f}/100 points"""

            comment_parts.append(breakdown)

        return "\n\n".join(comment_parts)

    def generate_batch_feedback_single_request(
        self,
        students: list[StudentParticipation],
        discussion_context: str,
        rubric_info: dict[str, float],
    ) -> dict[int, str]:
        """
        Generate feedback for ALL students in a single LLM API call.
        Much faster and cheaper than individual calls.

        Args:
            students: List of all student participation data
            discussion_context: Brief context about the discussion topic
            rubric_info: Grading rubric information

        Returns:
            Dictionary mapping student user_id to feedback text
        """
        try:
            # Load the Graderator system prompt
            system_prompt = _load_bundled_prompt("DISCUSSION_GRADERATOR.md")
            if system_prompt is None:
                system_prompt = self._create_system_prompt()

            # Build the batch user prompt with all students
            user_prompt = self._create_batch_prompt(students, discussion_context, rubric_info)

            response = self.llm_client.generate_sync(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=2000,  # Need more tokens for batch processing
            )

            # Parse the tab-separated response
            feedback_map = self._parse_batch_response(response.content, students)
            return feedback_map

        except Exception as e:
            # Fall back to per-student LLM calls if the batch request fails
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Batch LLM call failed, falling back to individual calls: {e}")

            feedback_map = {}
            # Normalize rubric keys for per-student generation
            per_student_rubric = {
                "substantial_post_points": rubric_info.get("substantial_post")
                or rubric_info.get("substantial_post_points", 60),
                "reply_points_each": rubric_info.get("reply_points")
                or rubric_info.get("reply_points_each", 20),
                "max_replies_counted": rubric_info.get("max_replies_counted")
                or rubric_info.get("min_replies", 2),
                "min_word_count": rubric_info.get("min_word_count", 300),
                "grading_profile": rubric_info.get("grading_profile"),
            }

            for student in students:
                try:
                    feedback_map[student.user_id] = self.generate_student_feedback(
                        student=student,
                        discussion_context=discussion_context,
                        rubric_info=per_student_rubric,
                        use_fallback_on_error=True,
                    )
                except Exception as single_err:
                    # Last-resort fallback
                    logger.warning(
                        f"Individual feedback generation failed for {student.user_name}: {single_err}"
                    )
                    feedback_map[student.user_id] = self._create_fallback_feedback(student)

            return feedback_map

    def _create_batch_prompt(
        self,
        students: list[StudentParticipation],
        discussion_context: str,
        rubric_info: dict[str, float],
    ) -> str:
        """Create a prompt for batch feedback generation.

        Like the per-student prompt, this follows whichever rubric
        actually graded the cohort: when any student carries explicit AI
        roles we describe the AI Discussion rubric (30/40/30) and list
        per-role word counts; otherwise we use the legacy model.
        """
        if any(self._has_ai_roles(student) for student in students) or ai_scoring.is_ai_discussion_rubric(
            None, rubric_info
        ):
            return self._create_ai_batch_prompt(students, discussion_context, rubric_info)

        # Build gradebook format input
        gradebook_lines = []
        gradebook_lines.append("GRADEBOOK FORMAT (Name\tScore\tDetails):")
        gradebook_lines.append("-" * 80)

        for student in students:
            # Participation details
            details = []
            if student.total_posts > 0:
                details.append(f"{student.total_posts} posts")
                details.append(f"{student.total_word_count} words total")
                details.append(f"{student.reply_count} replies")
            else:
                details.append("No participation")

            details_str = ", ".join(details)
            gradebook_lines.append(f"{student.user_name}\t{student.total_score:.1f}\t{details_str}")

        gradebook_data = "\\n".join(gradebook_lines)

        # Load template from file
        template = _load_bundled_prompt("FEEDBACK_BATCH.md")
        if template is not None:
            return template.format(
                discussion_context=discussion_context,
                substantial_points=rubric_info.get("substantial_post", 60),
                min_words=rubric_info.get("min_word_count", 300),
                reply_points=rubric_info.get("reply_points", 20),
                max_replies=rubric_info.get("min_replies", 2),
                total_points=rubric_info.get("total_points", 100),
                gradebook_data=gradebook_data,
            )

        # Fallback to hardcoded prompt
        context_lines = [
            "",
            "DISCUSSION CONTEXT:",
            f"Topic: {discussion_context}",
            "",
            "RUBRIC BREAKDOWN:",
            f"- Substantive post (300+ words): {rubric_info.get('substantial_post', 60)} points",
            f"- Each reply ({rubric_info.get('min_replies', 2)} required): {rubric_info.get('reply_points', 20)} points each",
            f"- Total possible: {rubric_info.get('total_points', 100)} points",
            "",
            "TASK:",
            "Generate personalized feedback for EACH student in the gradebook above.",
            "Output MUST be in tab-separated format: Name\\tScore\\tFeedback",
            "Output MUST include ALL students in the EXACT same order as the gradebook.",
            "Keep feedback to 1-2 sentences per student.",
            "",
        ]

        return "\\n".join(gradebook_lines + context_lines)

    def _create_ai_batch_prompt(
        self,
        students: list[StudentParticipation],
        discussion_context: str,
        rubric_info: dict[str, float],
    ) -> str:
        """Build the AI-Discussion-rubric-aware batch feedback prompt.

        Each gradebook row carries the per-role word counts
        (Research / Restatement / Reply) so the LLM can reference what
        the student actually did under the 30/40/30 rubric.
        """
        research_points = rubric_info.get("research_points", ai_scoring.RESEARCH_POINTS)
        restatement_points = rubric_info.get("restatement_points", ai_scoring.RESTATEMENT_POINTS)
        reply_points = rubric_info.get("reply_points", ai_scoring.REPLY_POINTS)

        gradebook_lines = []
        gradebook_lines.append("GRADEBOOK FORMAT (Name\tScore\tR1/R2/R3 words):")
        gradebook_lines.append("-" * 80)

        for student in students:
            if getattr(student, "post_details", None):
                role_words = ai_scoring.roles_from_post_details(student.post_details)
                details_str = (
                    f"{role_words['research']}/{role_words['restatement']}/"
                    f"{role_words['reply']} words (Research/Restatement/Reply)"
                )
            else:
                details_str = "No participation"
            gradebook_lines.append(f"{student.user_name}\t{student.total_score:.1f}\t{details_str}")

        gradebook_data = "\\n".join(gradebook_lines)

        template = _load_bundled_prompt("FEEDBACK_BATCH_AI.md")
        if template is not None:
            return template.format(
                discussion_context=discussion_context,
                research_points=f"{research_points:.0f}",
                restatement_points=f"{restatement_points:.0f}",
                reply_points=f"{reply_points:.0f}",
                gradebook_data=gradebook_data,
            )

        # Fallback to an inline AI-rubric batch prompt.
        context_lines = [
            "",
            "DISCUSSION CONTEXT:",
            f"Topic: {discussion_context}",
            "",
            "AI DISCUSSION RUBRIC (100 points total):",
            f"- Post 1 Research ({research_points:.0f} pts): deep-research prompts + full AI output",
            f"- Post 2 Restatement ({restatement_points:.0f} pts): 300+ words in the student's own words",
            f"- Post 3 Reply ({reply_points:.0f} pts): 150+ word substantive reply to a classmate",
            "Row details list per-role word counts as Research/Restatement/Reply (0 = missing).",
            "",
            "TASK:",
            "Generate personalized feedback for EACH student in the gradebook above.",
            "Ground each comment in the rubric; name what lost points plainly.",
            "Output MUST be tab-separated: Name\\tFeedback",
            "Output MUST include ALL students in the EXACT same order as the gradebook.",
            "Keep feedback to 1-2 sentences per student.",
            "",
        ]
        return "\\n".join(gradebook_lines + context_lines)

    def _parse_batch_response(
        self, response_text: str, students: list[StudentParticipation]
    ) -> dict[int, str]:
        """Parse tab-separated batch response into feedback map"""

        feedback_map = {}
        lines = response_text.strip().split("\n")

        # Try to match each line to a student
        for line in lines:
            if not line.strip() or "\t" not in line:
                continue

            parts = line.split("\t")
            if len(parts) < 3:
                continue

            name, _score, feedback = parts[0], parts[1], "\t".join(parts[2:])

            # Find matching student by name
            for student in students:
                if (
                    student.user_name.lower() in name.lower()
                    or name.lower() in student.user_name.lower()
                ):
                    feedback_map[student.user_id] = feedback.strip()
                    break

        # Fill in any missing students with fallback
        for student in students:
            if student.user_id not in feedback_map:
                feedback_map[student.user_id] = self._create_fallback_feedback(student)

        return feedback_map


def create_feedback_generator(
    provider: LLMProvider = LLMProvider.OPENAI,
    model: str | None = None,
    api_key: str | None = None,
) -> FeedbackGenerator:
    """
    Convenience function to create a feedback generator.

    Args:
        provider: LLM provider (openai or anthropic)
        model: Model name (uses defaults if not provided)
        api_key: API key (tries environment variables if not provided)

    Returns:
        Configured FeedbackGenerator instance
    """
    from coursewright.core.llm.client import create_client

    # Use faster, cheaper models for feedback generation
    if model is None:
        if provider == LLMProvider.OPENAI:
            model = "gpt-5-nano"  # Fastest and cheapest GPT-5 for feedback
        elif provider == LLMProvider.ANTHROPIC:
            model = "claude-3-5-haiku-20241022"  # Fast and economical

    client = create_client(
        provider=provider,
        model=model,
        api_key=api_key,
        temperature=0.7,
        max_tokens=300,  # Short feedback doesn't need many tokens
    )

    return FeedbackGenerator(client)
