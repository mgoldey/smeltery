"""Port: campaigns, their runs, their notebook entries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProjectStore(Protocol):
    def create_campaign(self, name: str, *, description: str = "") -> Any: ...

    def get_campaign(self, campaign_id: str) -> Any: ...

    def list_campaigns(self) -> list[Any]: ...

    def add_run(self, campaign_id: str, run_path: str | Path) -> None: ...

    def add_entry(self, entry: Any) -> Any:
        """Store a finished `NotebookEntry` (the caller makes its id, author and time). Append-only: an id that is
        already stored raises `ValueError`."""
        ...

    def entries(self, campaign_id: str) -> list[Any]:
        """A campaign's entries, oldest first."""
        ...

    def list_entries(self, campaign_id: str) -> list[Any]: ...

    def supersede(self, old_entry_id: str, new_entry: Any) -> Any: ...

    def add_proposal(self, proposal: Any) -> Any: ...

    def list_proposals(self, run: str | None = None) -> list[Any]: ...
