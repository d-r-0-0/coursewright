"""Packet Tracer submission classification and orchestration.

Detects Packet Tracer assignments in Canvas courses, checks that student
submissions hold the required files (a lab document plus a .pkt/.pka/.pkz
file), and orchestrates safe completeness feedback and 100% auto-grading.
Library code only: it returns data.
"""

from coursewright.ta.packettracer.classifier import (
    CompletenessReport,
    CompletenessVerdict,
    DetectionConfidence,
    DetectionResult,
    PacketTracerAssignmentDetector,
    SubmissionCompletenessChecker,
)
from coursewright.ta.packettracer.orchestrator import (
    REQUIRED_FEEDBACK_TEMPLATES,
    AutoGradeOutcome,
    AutoGradeResult,
    FeedbackPostOutcome,
    FeedbackPostResult,
    MissingTemplatesError,
    PacketTracerCheckSummary,
    auto_grade_complete_submissions,
    check_packet_tracer_assignment,
    fetch_existing_completeness_comments,
    post_completeness_feedback,
    templates_available,
)

__all__ = [
    "REQUIRED_FEEDBACK_TEMPLATES",
    "AutoGradeOutcome",
    "AutoGradeResult",
    "CompletenessReport",
    "CompletenessVerdict",
    "DetectionConfidence",
    "DetectionResult",
    "FeedbackPostOutcome",
    "FeedbackPostResult",
    "MissingTemplatesError",
    "PacketTracerAssignmentDetector",
    "PacketTracerCheckSummary",
    "SubmissionCompletenessChecker",
    "auto_grade_complete_submissions",
    "check_packet_tracer_assignment",
    "fetch_existing_completeness_comments",
    "post_completeness_feedback",
    "templates_available",
]
