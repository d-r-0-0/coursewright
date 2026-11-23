"""Phase 3: draft and create discussion prompts.

Two separate capabilities:

- `draft_discussion_starter_post` (LLM, no Canvas) in ``drafting``;
- `create_discussions` (safe Canvas writes, no LLM) in ``creation``.
"""

from coursewright.design.discussions.creation import (
    DiscussionCreationReport,
    create_discussions,
    select_unit_modules,
)
from coursewright.design.discussions.drafting import (
    DiscussionDraft,
    DraftingError,
    draft_discussion_starter_post,
)

__all__ = [
    "DiscussionCreationReport",
    "DiscussionDraft",
    "DraftingError",
    "create_discussions",
    "draft_discussion_starter_post",
    "select_unit_modules",
]
