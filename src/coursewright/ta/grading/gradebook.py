"""
Gradebook integration module for Canvas LMS
Handles grade submission and management through Canvas API

Reads go through the Canvas client.  Every grade *write* goes through the
core safe-write path (``SafeCanvasWriter``): a dry run unless the writer was
built with ``execute=True``, a timestamped JSON backup before each write, and
no silent failures (AGENTS.md 3.11).
"""

import logging
from dataclasses import dataclass, field

from coursewright.core.canvas.safe_write import (
    BackupFailedError,
    SafeCanvasWriter,
    SafeWriteError,
    WriteOutcome,
)
from coursewright.ta.grading import ai_scoring

logger = logging.getLogger(__name__)


@dataclass
class BatchGradeResult:
    """What a batch grade submission did (or, in a dry run, would do)."""

    successful: int = 0
    failed: int = 0
    previewed: int = 0
    outcomes: list[WriteOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class GradebookManager:
    """Manages gradebook operations for Canvas assignments"""

    def __init__(self, canvas_client, canvas_writer: SafeCanvasWriter):
        """
        Initialize gradebook manager

        Args:
            canvas_client: Canvas API client, used for reads only
            canvas_writer: Safe writer that performs every grade write
        """
        self.canvas_client = canvas_client
        self.canvas_writer = canvas_writer

    def get_assignment_for_discussion(self, course_id: int, discussion_id: int) -> dict | None:
        """
        Get the assignment associated with a discussion topic

        Args:
            course_id: Canvas course ID
            discussion_id: Discussion topic ID

        Returns:
            Assignment data or None if not found
        """
        try:
            assignment = self.canvas_client.get_assignment_by_discussion(course_id, discussion_id)
            if assignment:
                logger.info(f"Found assignment {assignment['id']} for discussion {discussion_id}")
                return assignment
            else:
                logger.warning(f"No assignment found for discussion {discussion_id}")
                return None
        except Exception as e:
            logger.error(f"Error getting assignment for discussion {discussion_id}: {e}")
            return None

    def get_current_grade(
        self, course_id: int, assignment_id: int, student_id: int
    ) -> float | None:
        """
        Get current grade for a student assignment

        Args:
            course_id: Canvas course ID
            assignment_id: Assignment ID
            student_id: Student user ID

        Returns:
            Current grade score or None if not found
        """
        try:
            submission = self.canvas_client.get_submission(course_id, assignment_id, student_id)

            if submission:
                return submission.get("score")
            else:
                logger.warning(
                    f"No submission found for student {student_id} in assignment {assignment_id}"
                )
                return None

        except Exception as e:
            logger.error(f"Error getting current grade for student {student_id}: {e}")
            return None

    def submit_grade(
        self, course_id: int, assignment_id: int, student_id: int, score: float, comment: str = None
    ) -> WriteOutcome:
        """
        Submit a grade for a student assignment through the safe writer

        Args:
            course_id: Canvas course ID
            assignment_id: Assignment ID
            student_id: Student user ID
            score: Grade score to submit
            comment: Optional grading comment

        Returns:
            The WriteOutcome: ``executed`` is False for a dry run, and True
            (with ``backup_path`` set) once Canvas accepted the grade.

        Raises:
            BackupFailedError: the backup failed, so nothing was written
            WriteFailedError: Canvas refused the grade (the backup is kept)
        """
        outcome = self.canvas_writer.submit_grade(
            course_id, assignment_id, student_id, score, comment
        )
        if outcome.executed:
            logger.info(
                f"Grade {score} submitted for student {student_id} in assignment {assignment_id}"
            )
        else:
            logger.info(
                f"Dry run: grade {score} for student {student_id} "
                f"in assignment {assignment_id} not sent"
            )
        return outcome

    def submit_multiple_grades(
        self, course_id: int, assignment_id: int, grades: list[dict]
    ) -> BatchGradeResult:
        """
        Submit multiple grades at once, each through the safe writer

        Args:
            course_id: Canvas course ID
            assignment_id: Assignment ID
            grades: List of dicts with 'student_id', 'score', and optional 'comment'

        Returns:
            A BatchGradeResult counting successful, failed and previewed
            (dry-run) grades, with the error text of every failure.

        Raises:
            BackupFailedError: a backup failed; the batch stops there so no
                grade is ever written without its backup.
        """
        result = BatchGradeResult()

        for grade in grades:
            try:
                outcome = self.submit_grade(
                    course_id,
                    assignment_id,
                    grade["student_id"],
                    grade["score"],
                    grade.get("comment"),
                )
            except BackupFailedError:
                raise
            except (SafeWriteError, ValueError) as err:
                result.failed += 1
                result.errors.append(f"student {grade.get('student_id')}: {err}")
                logger.error(f"Failed to submit grade for student {grade.get('student_id')}: {err}")
                continue

            result.outcomes.append(outcome)
            if outcome.executed:
                result.successful += 1
            else:
                result.previewed += 1

        logger.info(
            f"Batch grade submission: {result.successful} successful, "
            f"{result.failed} failed, {result.previewed} previewed"
        )
        return result

    def validate_grade_submission(self, assignment: dict, score: float) -> tuple[bool, str]:
        """
        Validate that a grade submission is valid

        Args:
            assignment: Assignment data from Canvas
            score: Grade score to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            max_points = assignment.get("points_possible", 100)

            if score < 0:
                return False, "Grade cannot be negative"

            if max_points and score > max_points:
                return False, f"Grade {score} exceeds maximum points {max_points}"

            return True, ""

        except Exception as e:
            return False, f"Validation error: {e}"

    def get_assignment_submissions(self, course_id: int, assignment_id: int) -> list[dict]:
        """
        Get all submissions for an assignment

        Args:
            course_id: Canvas course ID
            assignment_id: Assignment ID

        Returns:
            List of submission data
        """
        try:
            submissions = [
                submission.model_dump(mode="json")
                for submission in self.canvas_client.get_assignment_submissions(
                    course_id, assignment_id
                )
            ]
            logger.info(f"Found {len(submissions)} submissions for assignment {assignment_id}")
            return submissions

        except Exception as e:
            logger.error(f"Error getting submissions for assignment {assignment_id}: {e}")
            return []

    def create_grade_comment(
        self, analysis_results: dict, scoring_details: dict, participation: object | None = None
    ) -> str:
        """
        Create a detailed comment for grade submission

        Args:
            analysis_results: Discussion analysis results (legacy format)
            scoring_details: Scoring breakdown details
            participation: StudentParticipation object with detailed post info (optional)

        Returns:
            Formatted comment string
        """
        try:
            comment_parts = [
                "Scoring breakdown:",
                f"• Participation: {scoring_details.get('participation_score', 0):.1f} points",
                f"• Quality: {scoring_details.get('quality_score', 0):.1f} points",
                f"• Total: {scoring_details.get('total_score', 0):.1f} points",
                "",
            ]

            # If we have detailed participation data, show individual posts
            if (
                participation
                and hasattr(participation, "post_details")
                and participation.post_details
            ):
                comment_parts.append("Post details:")

                # Compute the actual largest post by word count so the
                # label and the 300-word requirement check always agree.
                # (We do NOT rely on the is_largest data flag, which may
                # be stale or set on the wrong post.)
                largest_word_count = max(p.get("word_count", 0) for p in participation.post_details)

                for post_detail in participation.post_details:
                    word_count = post_detail.get("word_count", 0)

                    # Mark the post whose word count matches the computed
                    # maximum as the "substantial" post.
                    if word_count == largest_word_count:
                        # Substantial post — label does NOT include word
                        # count because line below appends ": N words".
                        if word_count >= 300:
                            post_type = "Substantial post ✓"
                        else:
                            percentage = (word_count / 300 * 100) if word_count > 0 else 0
                            post_type = f"Substantial post ({percentage:.0f}% of requirement)"
                        # Ensure only the first matching post gets the
                        # label; any later ties are treated as replies.
                        largest_word_count = -1
                    else:
                        # This is a reply
                        reply_num = sum(
                            1
                            for p in participation.post_details
                            if p.get("post_number", 0) <= post_detail.get("post_number", 0)
                            and p.get("is_reply", False)
                        )
                        post_type = f"Reply {reply_num}"

                    comment_parts.append(f"• {post_type}: {word_count} words")

                # Recompute for the requirement check (largest_word_count
                # was mutated above to guard against ties).
                actual_largest_word_count = max(
                    p.get("word_count", 0) for p in participation.post_details
                )

                comment_parts.append("")
                comment_parts.append(f"Total word count: {participation.total_word_count}")
                comment_parts.append(f"Number of replies: {participation.reply_count}")

                # Add requirement status with more nuanced feedback
                if actual_largest_word_count < 300:
                    percentage = (
                        (actual_largest_word_count / 300 * 100)
                        if actual_largest_word_count > 0
                        else 0
                    )
                    comment_parts.append(
                        f"ℹ Largest post is {percentage:.0f}% of 300-word requirement"
                    )
                if participation.reply_count < 2:
                    comment_parts.append(f"⚠ Need {2 - participation.reply_count} more replies")

                # Only AI discussions get the three-post role block.  A
                # regular discussion has no Post 2 (Restatement)
                # requirement, so reporting one would invent a
                # requirement the student was never given.
                if ai_scoring.has_explicit_ai_roles(participation.post_details):
                    role_word_counts = ai_scoring.roles_from_post_details(
                        participation.post_details
                    )
                    timing_flags = [
                        post_detail.get("timing_status")
                        for post_detail in participation.post_details
                        if post_detail.get("timing_status")
                        in {"late_post2", "late_post3", "unknown"}
                    ]

                    comment_parts.append("")
                    comment_parts.append("AI Discussion Profile:")
                    comment_parts.append(
                        f"• Post 1 (Research): {role_word_counts['research']} words"
                    )
                    comment_parts.append(
                        f"• Post 2 (Restatement): {role_word_counts['restatement']} words"
                    )
                    comment_parts.append(f"• Post 3 (Reply): {role_word_counts['reply']} words")

                    if timing_flags:
                        comment_parts.append(
                            f"• Timing Flags: {', '.join(sorted(set(timing_flags)))}"
                        )

            else:
                # Fallback to legacy format
                comment_parts.extend(
                    [
                        f"Total word count: {analysis_results.get('word_count', 0)}",
                        f"Number of replies: {analysis_results.get('reply_count', 0)}",
                    ]
                )

            if analysis_results.get("late_submission"):
                comment_parts.append("• Late submission penalty applied")

            return "\n".join(comment_parts)

        except Exception as e:
            logger.error(f"Error creating grade comment: {e}")
            return "Automated discussion grading"

    def create_llm_enhanced_comment(
        self,
        student_participation,
        discussion_context: str,
        rubric_info: dict[str, float],
        llm_feedback_generator,
        include_score_breakdown: bool = True,
    ) -> str:
        """
        Create a grade comment enhanced with LLM-generated personalized feedback.

        Args:
            student_participation: StudentParticipation object with grading details
            discussion_context: Brief context about the discussion topic
            rubric_info: Dictionary with rubric information (points, requirements)
            llm_feedback_generator: FeedbackGenerator instance
            include_score_breakdown: Whether to include detailed score breakdown

        Returns:
            Formatted comment string with LLM-generated feedback
        """
        try:
            # If the score is somewhere in the middle (not a clean 0 or 100),
            # the role-classification heuristics may have mislabeled posts.
            # Ask the LLM for a second set of eyes and, if its mapping would
            # raise the student's score, apply it before generating feedback.
            # We never let the LLM *lower* a grade — always err toward the
            # student.
            role_review_note: str | None = None
            current_total = float(getattr(student_participation, "total_score", 0.0) or 0.0)
            if 0.0 < current_total < 100.0 and hasattr(
                llm_feedback_generator, "verify_role_assignment"
            ):
                role_review_note = self._maybe_apply_llm_role_review(
                    student_participation,
                    rubric_info,
                    llm_feedback_generator,
                )

            # Generate personalized feedback using LLM
            feedback = llm_feedback_generator.generate_student_feedback(
                student=student_participation,
                discussion_context=discussion_context,
                rubric_info=rubric_info,
            )

            comment_parts = [feedback]

            if role_review_note:
                comment_parts.append("")
                comment_parts.append(role_review_note)

            if include_score_breakdown:
                post_details = getattr(student_participation, "post_details", [])

                # An AI discussion earns points per role (30/40/30); a
                # regular discussion earns points for one substantial
                # post plus peer replies (60 + 20 + 20).  Show whichever
                # rubric actually produced the score, otherwise the
                # breakdown contradicts the grade the student sees.
                if ai_scoring.is_ai_discussion_rubric(post_details, rubric_info):
                    comment_parts.extend(self._build_ai_score_breakdown(post_details, rubric_info))
                else:
                    comment_parts.extend(
                        self._build_legacy_score_breakdown(student_participation, rubric_info)
                    )

            return "\n".join(comment_parts)

        except Exception as e:
            logger.error(f"Error creating LLM-enhanced comment: {e}")
            # Fallback to standard comment
            logger.info("Falling back to standard comment generation")
            return self._create_fallback_comment(student_participation)

    def _build_ai_score_breakdown(
        self, post_details: list[dict], rubric_info: dict[str, float]
    ) -> list[str]:
        """Render the AI discussion breakdown (Research / Restatement / Reply)."""
        role_word_counts = ai_scoring.roles_from_post_details(post_details)

        research_points = rubric_info.get("research_points", ai_scoring.RESEARCH_POINTS)
        restatement_points = rubric_info.get("restatement_points", ai_scoring.RESTATEMENT_POINTS)
        reply_points = rubric_info.get("reply_points", ai_scoring.REPLY_POINTS)
        research_min_words = rubric_info.get("min_word_count", 300)

        research_earned = ai_scoring.score_role(
            role_word_counts["research"], research_min_words, research_points
        )
        restatement_earned = ai_scoring.score_role(
            role_word_counts["restatement"],
            ai_scoring.RESTATEMENT_MIN_WORDS,
            restatement_points,
        )
        reply_earned = ai_scoring.score_role(
            role_word_counts["reply"], ai_scoring.REPLY_MIN_WORDS, reply_points
        )
        breakdown_total = round(research_earned + restatement_earned + reply_earned, 1)

        return [
            "",
            "Score Breakdown:",
            (
                f"• Post 1 (Research): {research_earned:.1f}/{research_points:.0f} points "
                f"({role_word_counts['research']} words)"
            ),
            (
                f"• Post 2 (Restatement): "
                f"{restatement_earned:.1f}/{restatement_points:.0f} points "
                f"({role_word_counts['restatement']} words)"
            ),
            (
                f"• Post 3 (Reply): {reply_earned:.1f}/{reply_points:.0f} points "
                f"({role_word_counts['reply']} words)"
            ),
            f"• Total: {breakdown_total:.1f}/100 points",
        ]

    def _build_legacy_score_breakdown(
        self, student_participation, rubric_info: dict[str, float]
    ) -> list[str]:
        """Render the regular-discussion breakdown (substantial post + replies).

        The totals come straight from the grade the analyzer already
        computed, so the breakdown can never disagree with the score
        posted to the gradebook.
        """
        substantial_points = rubric_info.get("substantial_post_points", 60.0)
        reply_points_each = rubric_info.get("reply_points_each", 20.0)
        max_replies_counted = int(rubric_info.get("max_replies_counted", 2))
        min_word_count = rubric_info.get("min_word_count", 300)

        substantial_words = int(getattr(student_participation, "initial_post_word_count", 0) or 0)
        reply_count = int(getattr(student_participation, "reply_count", 0) or 0)
        total_score = float(getattr(student_participation, "total_score", 0.0) or 0.0)

        substantial_earned = ai_scoring.score_role(
            substantial_words, min_word_count, substantial_points
        )
        reply_max_points = max_replies_counted * reply_points_each
        reply_earned = min(reply_count, max_replies_counted) * reply_points_each

        return [
            "",
            "Score Breakdown:",
            (
                f"• Substantial post: {substantial_earned:.1f}/{substantial_points:.0f} points "
                f"({substantial_words} words)"
            ),
            (
                f"• Replies: {reply_earned:.1f}/{reply_max_points:.0f} points "
                f"({reply_count} replies)"
            ),
            f"• Total: {total_score:.1f}/100 points",
        ]

    def _maybe_apply_llm_role_review(
        self,
        student_participation,
        rubric_info: dict,
        llm_feedback_generator,
    ) -> str | None:
        """Ask the LLM to verify role assignments; regrade if it scores higher.

        Returns a short note describing the LLM's review when an
        adjustment is made (so it can be appended to the comment), or
        ``None`` when no change was applied.
        """
        post_details = getattr(student_participation, "post_details", None) or []
        if len(post_details) < 2:
            return None

        # Role review only makes sense for AI discussions.  A regular
        # discussion has no Post 1/2/3 roles to re-map, and rescoring it
        # against the 30/40/30 rubric would corrupt a correct grade.
        if not ai_scoring.has_explicit_ai_roles(post_details):
            return None

        try:
            mapping = llm_feedback_generator.verify_role_assignment(student_participation)
        except Exception as exc:  # defensive — verify_role_assignment already swallows
            logger.warning(f"LLM role verification raised unexpectedly: {exc}")
            return None
        if not mapping:
            return None

        post_by_number = {pd.get("post_number"): pd for pd in post_details}
        suggested_research = post_by_number.get(mapping.get("research"))
        suggested_restatement = post_by_number.get(mapping.get("restatement"))
        suggested_reply = post_by_number.get(mapping.get("reply"))

        # Distinct picks only — the LLM cannot assign one post to two roles.
        picks = [
            suggested_research,
            suggested_restatement,
            suggested_reply,
        ]
        non_null_picks = [p for p in picks if p is not None]
        if len({id(p) for p in non_null_picks}) != len(non_null_picks):
            logger.debug("LLM role review returned duplicate posts; ignoring")
            return None

        research_min_words = int(rubric_info.get("min_word_count", 300) or 300)

        proposed_breakdown = ai_scoring.compute_ai_scores(
            research_words=(suggested_research.get("word_count", 0) if suggested_research else 0),
            restatement_words=(
                suggested_restatement.get("word_count", 0) if suggested_restatement else 0
            ),
            reply_words=(suggested_reply.get("word_count", 0) if suggested_reply else 0),
            research_min_words=research_min_words,
        )
        proposed_total = proposed_breakdown["total_score"]
        current_total = float(getattr(student_participation, "total_score", 0.0) or 0.0)

        if proposed_total <= current_total:
            # Never lower a student's grade based on the LLM second look.
            return None

        # Apply the new mapping: rewrite post_role on each post_detail.
        new_roles_by_id = {}
        if suggested_research is not None:
            new_roles_by_id[id(suggested_research)] = "research"
        if suggested_restatement is not None:
            new_roles_by_id[id(suggested_restatement)] = "restatement"
        if suggested_reply is not None:
            new_roles_by_id[id(suggested_reply)] = "reply"
        for post_detail in post_details:
            post_detail["post_role"] = new_roles_by_id.get(id(post_detail), "extra")
            # Keep is_initial / is_largest compatible with the new role.
            post_detail["is_initial"] = post_detail["post_role"] == "research"
            post_detail["is_largest"] = post_detail["is_initial"]

        # Recompute participation score & band so downstream consumers
        # (Score Breakdown, gradebook export) see the corrected values.
        roles_present_count = sum(
            1
            for pick in (suggested_research, suggested_restatement, suggested_reply)
            if pick is not None
        )
        restatement_words = (
            suggested_restatement.get("word_count", 0) if suggested_restatement else 0
        )
        reply_words = suggested_reply.get("word_count", 0) if suggested_reply else 0
        new_band = ai_scoring.determine_band(
            roles_present_count=roles_present_count,
            restatement_words=restatement_words,
            reply_words=reply_words,
        )

        student_participation.total_score = proposed_total
        student_participation.participation_score = proposed_total
        student_participation.quality_score = proposed_total
        student_participation.band = new_band
        student_participation.meets_requirements = new_band in {"excellent", "good"}

        # Record what happened on band_reasons for traceability.
        rationale = (mapping.get("rationale") or "").strip()
        reasons = getattr(student_participation, "band_reasons", None)
        if isinstance(reasons, list):
            reasons.append(
                f"LLM role review raised score "
                f"{current_total:.1f} → {proposed_total:.1f}: {rationale}"
            )

        delta = proposed_total - current_total
        note = (
            f"🤖 LLM role review adjusted the role mapping "
            f"(+{delta:.1f} pts: {current_total:.1f} → {proposed_total:.1f})."
        )
        if rationale:
            note += f" Rationale: {rationale}"
        return note

    def _create_fallback_comment(self, student_participation) -> str:
        """
        Create a simple fallback comment if LLM generation fails.

        Args:
            student_participation: StudentParticipation object

        Returns:
            Simple formatted comment
        """
        comment_parts = [
            f"Total Score: {student_participation.total_score:.1f}/100 points",
            f"Substantial Post: {student_participation.initial_post_word_count} words",
            f"Replies: {student_participation.reply_count}",
        ]

        if not student_participation.meets_requirements:
            comment_parts.append("")
            comment_parts.append("Requirements not fully met.")
            if student_participation.missing_requirements:
                comment_parts.append(
                    f"Missing: {', '.join(student_participation.missing_requirements)}"
                )

        return "\n".join(comment_parts)
