"""
NetAcad Export Parser
=====================
Handles robust parsing of NetAcad grade exports with delimiter sniffing
and data normalization.

"""

import logging
from pathlib import Path

import pandas as pd

# Get logger for this module
logger = logging.getLogger(__name__)


class NetacadExportParser:
    """
    Parser for Cisco NetAcad CSV/TSV grade exports.

    This class handles the messy reality of various export formats,
    including different delimiters and extra rows like "Points Possible".
    """

    def __init__(self):
        """Initialize the parser with empty state."""
        self.detected_delimiter: str | None = None
        self.raw_dataframe: pd.DataFrame | None = None
        self.column_headers: list[str] = []

    def sniff_delimiter_from_content(self, preview_content: str) -> str:
        """
        Detect the most likely delimiter from a sample of the file content.

        It looks for consistency in column counts across the first few lines
        to distinguish between comma, tab, and semicolon.

        Args:
            preview_content: A string containing the first few thousand bytes of the file.

        Returns:
            The detected delimiter character (defaulting to comma).
        """
        lines_to_check = preview_content.splitlines()[:20]
        if not lines_to_check:
            return ","

        candidate_delimiters = [",", "\t", ";"]
        best_candidate = ","
        highest_consistency_score = -1

        for delimiter in candidate_delimiters:
            # Count occurrences of this delimiter in each line that actually contains it
            occurrence_counts = [
                line.count(delimiter) for line in lines_to_check if delimiter in line
            ]
            if not occurrence_counts:
                continue

            # Use a frequency map to find the most consistent count
            from collections import Counter

            frequency_map = Counter(occurrence_counts)
            most_common_count, frequency = frequency_map.most_common(1)[0]

            # We favor delimiters that appear consistently with the same count across lines
            if frequency > highest_consistency_score:
                highest_consistency_score = frequency
                best_candidate = delimiter
            elif frequency == highest_consistency_score and most_common_count > (
                lines_to_check[0].count(best_candidate)
            ):
                # Tie-breaker: prefer the one that yields more columns
                best_candidate = delimiter

        return best_candidate

    def parse_export_file(self, file_path: Path) -> pd.DataFrame:
        """
        Robustly read the NetAcad export file into a pandas DataFrame.

        Args:
            file_path: Path to the CSV/TSV file exported from NetAcad.

        Returns:
            A pandas DataFrame containing the parsed data.
        """
        try:
            # 1. Read a small chunk to sniff the delimiter
            with open(file_path, encoding="utf-8", errors="replace") as file_handle:
                file_preview = file_handle.read(4096)
                self.detected_delimiter = self.sniff_delimiter_from_content(file_preview)

            logger.info(f"Detected delimiter '{self.detected_delimiter}' for file: {file_path}")

            # 2. Parse the full file with pandas
            # We treat everything as strings initially to avoid premature type casting
            self.raw_dataframe = pd.read_csv(
                file_path,
                sep=self.detected_delimiter,
                encoding="utf-8",
                encoding_errors="replace",
                dtype=str,
            )

            # 3. Clean up column headers (remove whitespace)
            self.raw_dataframe.columns = [column.strip() for column in self.raw_dataframe.columns]
            self.column_headers = list(self.raw_dataframe.columns)

            return self.raw_dataframe

        except Exception as error:
            logger.error(f"Critical failure while parsing NetAcad export {file_path}: {error}")
            raise

    def extract_clean_student_records(self) -> list[dict]:
        """
        Filter the raw data to extract actual student records.

        This method removes:
        - The "Points Possible" row if present
        - Test students (identified by name patterns)
        - Empty rows

        Returns:
            A list of dictionaries, where each dict represents one student's grades.
        """
        if self.raw_dataframe is None:
            logger.warning("Attempted to extract records before parsing a file.")
            return []

        clean_records = []

        for _, row_series in self.raw_dataframe.iterrows():
            record_dict = row_series.to_dict()

            # Identify name and email values for filtering
            # NetAcad typically uses 'NAME' and 'EMAIL' (uppercase)
            student_name = str(record_dict.get("NAME", "")).lower()
            student_email = str(record_dict.get("EMAIL", "")).lower()

            # Skip rows that represent metadata rather than students
            if "points possible" in student_name or "points possible" in student_email:
                logger.debug("Skipping metadata row: Points Possible")
                continue

            # Filter out common test student identifiers
            is_test_student = any(
                pattern in student_name
                for pattern in ["test student", "student, test", "test, student"]
            )
            if is_test_student:
                logger.debug(f"Skipping test student: {record_dict.get('NAME')}")
                continue

            # If the name is blank, it's probably an empty trailing row
            if not student_name.strip():
                continue

            clean_records.append(record_dict)

        return clean_records

    def get_potential_assignment_headers(self) -> list[str]:
        """
        Identify columns that likely contain assignment scores.

        Excludes standard identity columns like NAME and EMAIL.

        Returns:
            List of column names that correspond to exams or assignments.
        """
        if not self.column_headers:
            return []

        identity_column_keywords = [
            "NAME",
            "EMAIL",
            "ID",
            "USER ID",
            "SIS ID",
            "SIS LOGIN ID",
            "EXTERNAL ID",
            "USERNAME",
        ]

        assignment_headers = [
            header
            for header in self.column_headers
            if header.upper() not in identity_column_keywords
        ]

        return assignment_headers
