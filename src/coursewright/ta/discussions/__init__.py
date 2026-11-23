"""Discussion analysis and drafted instructor replies.

Analysis and drafting are read-only.  Posting a reply is the separate
``post_drafted_reply`` call, which is a dry run unless the safe writer was
built with ``execute=True``.
"""

from .daily_digest import (
    DailyDigest,
    DigestError,
    DigestFiles,
    InstructorMentionMatcher,
    build_daily_digest,
    write_daily_digest,
)
from .post_analyzer import AnalysisFlag, PostAnalyzer, PostRanking
from .prompts import (
    POST_ANALYSIS_PROMPT,
    POST_SUMMARY_PROMPT,
    RANKING_PROMPT,
    RESPONSE_DRAFT_PROMPT,
    RESPONSE_REFINEMENT_PROMPT,
)
from .reply_poster import post_drafted_reply
from .response_generator import ResponseGenerator
from .similarity_checker import SimilarityChecker

__all__ = [
    "DailyDigest",
    "DigestError",
    "DigestFiles",
    "InstructorMentionMatcher",
    "build_daily_digest",
    "write_daily_digest",
    "PostAnalyzer",
    "PostRanking",
    "AnalysisFlag",
    "ResponseGenerator",
    "SimilarityChecker",
    "post_drafted_reply",
    "POST_ANALYSIS_PROMPT",
    "POST_SUMMARY_PROMPT",
    "RANKING_PROMPT",
    "RESPONSE_DRAFT_PROMPT",
    "RESPONSE_REFINEMENT_PROMPT",
]
