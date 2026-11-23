"""Reusable selectable table widget for toggle-style row selection.

`SelectionTable` wraps a Textual `DataTable` and provides a compact API
for loading rows, toggling selection by index, and retrieving selected
values. It is designed for workflows that need checkbox-like behaviour,
such as assignment pickers.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.containers import Vertical
from textual.widgets import DataTable, Static


@dataclass
class SelectionRow:
    """One row in the selection table.

    Attributes:
        row_identifier: Stable key returned when selected.
        title: Primary label shown to the user.
        detail: Secondary context (column name, summary text, etc.).
        metric: Right-aligned metric string (coverage, recipient count).
        selected: Whether this row starts selected.
    """

    row_identifier: int
    title: str
    detail: str
    metric: str
    selected: bool = False


class SelectionTable(Vertical):
    """Toggle-table widget that renders ✓ / · selection status.

    Keyboard expectations are intentionally simple:
    - Up/Down: move cursor (handled by DataTable)
    - Space: toggle highlighted row
    - a: select all rows
    - n: clear all rows
    """

    def __init__(
        self,
        rows: list[SelectionRow],
        table_id: str = "selection-table",
        status_id: str = "selection-status",
    ) -> None:
        super().__init__()
        self._rows = rows
        self._selected_indices = {index for index, row in enumerate(rows) if row.selected}
        self.table_id = table_id
        self.status_id = status_id

    def compose(self):
        """Compose the table and status line."""
        yield DataTable(id=self.table_id, cursor_type="row")
        yield Static(id=self.status_id)

    def on_mount(self) -> None:
        """Configure table columns and draw initial rows."""
        table = self.query_one(DataTable)
        table.add_columns("#", "Sel", "Item", "Detail", "Metric")
        table.focus()
        self.refresh_rows()

    def refresh_rows(self) -> None:
        """Redraw all rows with current selection checkmarks."""
        table = self.query_one(DataTable)
        table.clear(columns=False)

        for index, selection_row in enumerate(self._rows):
            checkmark = "✓" if index in self._selected_indices else "·"
            table.add_row(
                str(index + 1),
                checkmark,
                selection_row.title,
                selection_row.detail,
                selection_row.metric,
            )

        self._update_status_text()

    def toggle_index(self, index: int) -> bool:
        """Toggle one row by zero-based index.

        Returns:
            True if the index was valid and toggled, else False.
        """
        if index < 0 or index >= len(self._rows):
            return False

        if index in self._selected_indices:
            self._selected_indices.remove(index)
        else:
            self._selected_indices.add(index)

        self.refresh_rows()
        return True

    def toggle_cursor_row(self) -> bool:
        """Toggle the row currently highlighted in the DataTable."""
        table = self.query_one(DataTable)
        row_index: int | None = table.cursor_row
        if row_index is None:
            return False
        return self.toggle_index(row_index)

    def select_all(self) -> None:
        """Mark every row as selected."""
        self._selected_indices = set(range(len(self._rows)))
        self.refresh_rows()

    def clear_all(self) -> None:
        """Clear selection across all rows."""
        self._selected_indices.clear()
        self.refresh_rows()

    def selected_identifiers(self) -> set[int]:
        """Return row identifiers for currently selected rows."""
        return {self._rows[index].row_identifier for index in sorted(self._selected_indices)}

    def _update_status_text(self) -> None:
        status = self.query_one(Static)
        selected_count = len(self._selected_indices)
        total_count = len(self._rows)
        status.update(
            f"[dim]{selected_count} of {total_count} selected (Space toggle, A all, N none).[/dim]"
        )
