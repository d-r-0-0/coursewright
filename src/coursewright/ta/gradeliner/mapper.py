"""
Mapping Manager
===============
Handles the complex logic of matching assignments and students between
NetAcad and Canvas using LLM-assisted fuzzy matching.
"""

import json
import logging
import re
from pathlib import Path

from coursewright.core.canvas.models import Assignment, CanvasUser
from coursewright.core.config.manager import resolve_workspace_directory
from coursewright.core.llm.client import LLMClient
from coursewright.core.llm.prompt_library import load_prompt
from coursewright.ta.gradeliner.models import (
    AssignmentMapCache,
    AssignmentMapping,
    StudentMapCache,
    StudentMapping,
)

# Get logger for this module
logger = logging.getLogger(__name__)

_MIN_MAPPING_COMPLETION_TOKENS = 4000


class MappingManager:
    """
    Orchestrates identity and assignment matching between NetAcad and Canvas.

    It uses LLM calls for initial fuzzy matching and maintains local JSON
    caches to avoid redundant calls in future runs.
    """

    def __init__(
        self,
        course_code: str,
        llm_client: LLMClient | None,
        export_root: Path | None = None,
    ):
        """
        Initialize the mapper for a specific course.

        Args:
            course_code: The Canvas course code (used for folder names).
            llm_client: An initialized LLM client for matching.
                May be None when only loading cached mappings.
            export_root: Folder holding one sub-folder per course for the
                mapping caches. Defaults to ``exports`` inside the private
                workspace; no roster cache is created in the checkout.
        """
        self.course_code = course_code
        self.llm_client = llm_client

        # Sanitize course code for filesystem use
        safe_course_folder = re.sub(r"[^\w.-]", "_", course_code).strip(".") or "course"
        if export_root is None:
            workspace_directory = resolve_workspace_directory()
            if workspace_directory is None:
                raise ValueError(
                    "No private workspace is configured; set COURSEWRIGHT_WORKSPACE "
                    "so grade mappings have somewhere to go."
                )
            export_root = workspace_directory / "exports"
        self.export_directory = export_root / safe_course_folder
        self.export_directory.mkdir(parents=True, exist_ok=True)

        self.assignment_cache_path = self.export_directory / "gradeliner_assignment_map.json"
        self.student_cache_path = self.export_directory / "gradeliner_student_map.json"

    # ------------------------------------------------------------------
    # Cache Persistence
    # ------------------------------------------------------------------

    def load_assignment_cache(self) -> AssignmentMapCache | None:
        """Load cached assignment mappings if they exist."""
        if not self.assignment_cache_path.exists():
            return None
        try:
            return AssignmentMapCache.model_validate_json(self.assignment_cache_path.read_text())
        except Exception as error:
            logger.error(f"Failed to load assignment cache: {error}")
            return None

    def save_assignment_cache(self, cache: AssignmentMapCache) -> None:
        """Persist assignment mappings to disk."""
        self.assignment_cache_path.write_text(cache.model_dump_json(indent=2))

    def load_student_cache(self) -> StudentMapCache | None:
        """Load cached student mappings if they exist."""
        if not self.student_cache_path.exists():
            return None
        try:
            return StudentMapCache.model_validate_json(self.student_cache_path.read_text())
        except Exception as error:
            logger.error(f"Failed to load student cache: {error}")
            return None

    def save_student_cache(self, cache: StudentMapCache) -> None:
        """Persist student mappings to disk."""
        self.student_cache_path.write_text(cache.model_dump_json(indent=2))

    # ------------------------------------------------------------------
    # LLM-Assisted Mapping
    # ------------------------------------------------------------------

    def _mapping_completion_tokens(self) -> int:
        """Return a completion budget large enough for mapping tables.

        GPT reasoning models count hidden reasoning against the same
        completion budget as visible TSV output.  Use at least a few
        thousand tokens for mapping tasks, but respect a larger value if
        the user configured one on the shared LLM client.
        """
        configured_tokens = getattr(
            getattr(self.llm_client, "config", None),
            "max_tokens",
            _MIN_MAPPING_COMPLETION_TOKENS,
        )
        try:
            configured_token_count = int(configured_tokens)
        except (TypeError, ValueError):
            configured_token_count = _MIN_MAPPING_COMPLETION_TOKENS
        return max(configured_token_count, _MIN_MAPPING_COMPLETION_TOKENS)

    # ------------------------------------------------------------------
    # Unit-Code Helpers (U01T01-style stable prefixes)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_unit_code(text: str) -> str | None:
        """
        Extract the first ``U##X##``-style unit code found in *text*.

        Instructors embed codes like ``U01T01`` or ``U03D01`` at the
        start of both Canvas assignment names and NetAcad column headers.
        These codes remain stable even when the rest of the name changes,
        so they give us a deterministic, LLM-free match key.

        Examples::

            _extract_unit_code("U01T01: Intro to Networks")  # → "U01T01"
            _extract_unit_code("Module 1 Exam")              # → None

        Args:
            text: Any string to search.

        Returns:
            Upper-cased code (e.g. ``"U01T01"``), or ``None`` if absent.
        """
        match = re.search(r"\b([A-Za-z]\d+[A-Za-z]\d+)\b", text)
        return match.group(1).upper() if match else None

    # ------------------------------------------------------------------
    # Cache Reconciliation (stale-name repair)
    # ------------------------------------------------------------------

    def reconcile_assignment_names(self, canvas_assignments: list[Assignment]) -> list[str]:
        """
        Update any stale ``canvas_assignment_name`` values in cached mappings.

        Canvas assignment IDs are stable forever; display names can be
        renamed by instructors at any time.  This method re-syncs every
        cached mapping against the live Canvas assignment list (keyed by
        ID) and persists the updated cache only when changes are found.

        Call this before reading the cache for display purposes so the
        TUI always shows current names even after a rename.

        Args:
            canvas_assignments: The current list of assignments from Canvas.

        Returns:
            A list of human-readable change descriptions, e.g.
            ``'"OldName" → "NewName"'``.  Empty if nothing changed.
        """
        cache = self.load_assignment_cache()
        if not cache:
            return []

        canvas_by_id = {a.id: a for a in canvas_assignments}
        changes: list[str] = []

        for mapping in cache.mappings:
            live_assignment = canvas_by_id.get(mapping.canvas_assignment_id)
            if not live_assignment:
                # Assignment may have been deleted from Canvas; leave it alone.
                continue

            if live_assignment.name != mapping.canvas_assignment_name:
                stale_name = mapping.canvas_assignment_name
                mapping.canvas_assignment_name = live_assignment.name
                # Re-sync points_possible in case that also changed.
                mapping.canvas_points_possible = (
                    live_assignment.points_possible or mapping.canvas_points_possible
                )
                changes.append(f'"{stale_name}" \u2192 "{live_assignment.name}"')

        if changes:
            self.save_assignment_cache(cache)
            logger.info(
                "Reconciled %d renamed Canvas assignment(s): %s",
                len(changes),
                ", ".join(changes),
            )

        return changes

    def propose_assignment_mapping(
        self, netacad_columns: list[str], canvas_assignments: list[Assignment]
    ) -> list[AssignmentMapping]:
        """
        Propose a 1:1 mapping between NetAcad columns and Canvas assignments.

        Uses a two-phase strategy:

        1. **Unit-code pre-pass** — columns and assignment names that share
           a ``U##X##`` prefix code (e.g. ``U01T01``) are matched instantly
           without an LLM call.  This is deterministic and free.

        2. **LLM fallback** — any columns that did not match via unit-code
           are sent to the LLM along with the remaining Canvas assignments.

        If every column is matched in phase 1, the LLM is never called.
        """
        # --- Phase 1: deterministic unit-code pre-pass -------------------
        pre_matched: list[AssignmentMapping] = []
        unmatched_netacad_columns: list[str] = []
        used_canvas_ids: set[int] = set()

        # Build a lookup: unit_code → Canvas Assignment
        canvas_by_unit_code: dict[str, Assignment] = {}
        for canvas_assignment in canvas_assignments:
            code = self._extract_unit_code(canvas_assignment.name)
            if code:
                canvas_by_unit_code[code] = canvas_assignment

        for netacad_column in netacad_columns:
            code = self._extract_unit_code(netacad_column)
            if code and code in canvas_by_unit_code:
                canvas_assignment = canvas_by_unit_code[code]
                pre_matched.append(
                    AssignmentMapping(
                        netacad_column=netacad_column,
                        canvas_assignment_name=canvas_assignment.name,
                        canvas_assignment_id=canvas_assignment.id,
                        canvas_points_possible=canvas_assignment.points_possible or 0.0,
                        confidence="high",
                    )
                )
                used_canvas_ids.add(canvas_assignment.id)
                logger.debug("Unit-code pre-match: %r → %r", netacad_column, canvas_assignment.name)
            else:
                unmatched_netacad_columns.append(netacad_column)

        if pre_matched:
            logger.info(
                "Unit-code pre-pass matched %d/%d assignment(s); %d sent to LLM.",
                len(pre_matched),
                len(netacad_columns),
                len(unmatched_netacad_columns),
            )

        # If everything was matched deterministically, skip the LLM call.
        if not unmatched_netacad_columns:
            return pre_matched

        # --- Phase 2: LLM fallback for unmatched columns -----------------
        remaining_canvas = [a for a in canvas_assignments if a.id not in used_canvas_ids]

        system_prompt = self._load_prompt("ASSIGNMENT_MAP_SYSTEM.md")

        # Format the user prompt with the specific lists
        canvas_info = [
            {"id": a.id, "name": a.name, "points_possible": a.points_possible}
            for a in remaining_canvas
        ]

        user_prompt_template = self._load_prompt("ASSIGNMENT_MAP_USER.md")
        user_prompt = user_prompt_template.format(
            netacad_columns=json.dumps(unmatched_netacad_columns, indent=2),
            canvas_assignments=json.dumps(canvas_info, indent=2),
        )

        logger.info("Requesting assignment mapping from LLM...")
        response = self.llm_client.generate_sync(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.0,  # We want deterministic matching
            max_tokens=self._mapping_completion_tokens(),
        )

        llm_matched = self._parse_assignment_llm_response(response.content, remaining_canvas)
        return pre_matched + llm_matched

    def propose_student_mapping(
        self, netacad_students: list[dict], canvas_roster: list[CanvasUser]
    ) -> list[StudentMapping]:
        """
        Call the LLM to match NetAcad names/emails to the Canvas roster.
        """
        system_prompt = self._load_prompt("STUDENT_MAP_SYSTEM.md")

        # Format the user prompt
        canvas_info = [
            {"id": u.id, "name": u.name, "login_id": u.login_id, "email": u.email}
            for u in canvas_roster
        ]

        user_prompt_template = self._load_prompt("STUDENT_MAP_USER.md")
        user_prompt = user_prompt_template.format(
            netacad_students=json.dumps(netacad_students, indent=2),
            canvas_roster=json.dumps(canvas_info, indent=2),
        )

        logger.info("Requesting student mapping from LLM...")
        response = self.llm_client.generate_sync(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.0,
            max_tokens=self._mapping_completion_tokens(),
        )

        return self._parse_student_llm_response(response.content, canvas_roster)

    # ------------------------------------------------------------------
    # Parsing LLM Responses (Tab-Separated)
    # ------------------------------------------------------------------

    def _parse_assignment_llm_response(
        self, response_text: str, canvas_assignments: list[Assignment]
    ) -> list[AssignmentMapping]:
        """Parse the TSV output from the LLM into AssignmentMapping objects."""
        mappings = []
        canvas_lookup = {a.id: a for a in canvas_assignments}

        for line in response_text.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue

            net_col, canvas_name, canvas_id_str = parts[0], parts[1], parts[2]
            confidence = parts[3] if len(parts) > 3 else "medium"

            if canvas_name == "UNMAPPED" or canvas_id_str == "0":
                continue

            try:
                # Strip potential surrounding text if LLM was chatty
                clean_id = "".join(filter(str.isdigit, canvas_id_str))
                if not clean_id:
                    continue

                canvas_id = int(clean_id)
                if canvas_id in canvas_lookup:
                    assignment = canvas_lookup[canvas_id]
                    mappings.append(
                        AssignmentMapping(
                            netacad_column=net_col,
                            canvas_assignment_name=assignment.name,
                            canvas_assignment_id=assignment.id,
                            canvas_points_possible=assignment.points_possible or 0.0,
                            confidence=confidence.lower(),
                        )
                    )
            except ValueError:
                continue

        return mappings

    def _parse_student_llm_response(
        self, response_text: str, canvas_roster: list[CanvasUser]
    ) -> list[StudentMapping]:
        """Parse the TSV output from the LLM into StudentMapping objects."""
        mappings = []
        canvas_lookup = {u.id: u for u in canvas_roster}

        for line in response_text.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue

            net_name, net_email, canvas_name, canvas_id_str = parts[0], parts[1], parts[2], parts[3]
            method = parts[4] if len(parts) > 4 else "unknown"
            confidence = parts[5] if len(parts) > 5 else "medium"

            if canvas_name == "UNMATCHED" or canvas_id_str == "0":
                continue

            try:
                clean_id = "".join(filter(str.isdigit, canvas_id_str))
                if not clean_id:
                    continue

                canvas_id = int(clean_id)
                if canvas_id in canvas_lookup:
                    user = canvas_lookup[canvas_id]
                    mappings.append(
                        StudentMapping(
                            netacad_name=net_name,
                            netacad_email=net_email,
                            canvas_user_id=user.id,
                            canvas_name=user.name,
                            canvas_login_id=user.login_id or "",
                            match_method=method,
                            confidence=confidence.lower(),
                        )
                    )
            except ValueError:
                continue

        return mappings

    def _load_prompt(self, filename: str) -> str:
        """Load a bundled prompt template (``core/llm/prompts``)."""
        return load_prompt(filename)
