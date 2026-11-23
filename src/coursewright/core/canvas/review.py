"""Bind a remote operation to the complete inputs an instructor reviewed."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


class ReviewChangedError(RuntimeError):
    """The remote inputs changed and need another review before any writes."""


@dataclass
class _ResponseSnapshot:
    status_code: int
    body: Any
    links: dict
    headers: dict = field(compare=False)

    def json(self):
        return deepcopy(self.body)


def _snapshot(value: Any) -> Any:
    if hasattr(value, "status_code") and callable(getattr(value, "json", None)):
        return _ResponseSnapshot(
            value.status_code,
            deepcopy(value.json()),
            deepcopy(getattr(value, "links", {})),
            deepcopy(getattr(value, "headers", {})),
        )
    return deepcopy(value)


@dataclass
class ReviewedCanvasInputs:
    """Record full read results, revalidate remotely, then replay reviewed data.

    Replaying prevents a second planning pass from silently discovering new
    targets after validation. Additional reads needed only for per-write
    backups still use the real client. Nothing here stores data on disk.
    """

    client: Any
    reads: list[tuple[str, tuple, dict, Any]] = field(default_factory=list)
    replay: bool = False

    def validate(self) -> None:
        """Read every recorded input again before allowing the first write."""
        for name, args, kwargs, reviewed in self.reads:
            current = _snapshot(getattr(self.client, name)(*args, **kwargs))
            if current != reviewed:
                raise ReviewChangedError("Canvas changed since preview. Preview this phase again.")
        self.replay = True

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.client, name)
        if not callable(attribute):
            return attribute

        def call(*args, **kwargs):
            is_read = (
                name.startswith("get_")
                or name == "_paginated_get"
                or (name == "_make_request" and args and args[0].upper() == "GET")
            )
            if not is_read:
                if not self.replay:
                    raise ReviewChangedError("A preview attempted a Canvas write.")
                return attribute(*args, **kwargs)
            if self.replay:
                for read_name, read_args, read_kwargs, value in self.reads:
                    if (name, args, kwargs) == (read_name, read_args, read_kwargs):
                        return deepcopy(value)
                # SafeCanvasWriter fetches individual resources for backups.
                return attribute(*args, **kwargs)
            result = _snapshot(attribute(*args, **kwargs))
            for read_name, read_args, read_kwargs, value in self.reads:
                if (name, args, kwargs) == (read_name, read_args, read_kwargs) and value != result:
                    raise ReviewChangedError("Canvas changed during preview. Preview again.")
            self.reads.append((name, deepcopy(args), deepcopy(kwargs), result))
            return deepcopy(result)

        return call
