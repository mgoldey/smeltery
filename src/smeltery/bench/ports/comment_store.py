"""Port: threads anchored to a run, candidate or pose."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Protocol


@dataclass
class Comment:
    id: str
    anchor_type: str  # run | candidate | pose
    anchor_id: str
    author: str
    body: str
    created_at: datetime
    parent_id: str | None = None
    run: str | None = None
    anchor_text: str = ""

    @property
    def anchor(self) -> str:
        if self.anchor_text:
            return self.anchor_text
        if self.anchor_type and self.anchor_id:
            return f"{self.anchor_type}:{self.anchor_id}"
        return self.anchor_id or self.anchor_type or ""


class CommentStore(Protocol):
    def add(
        self,
        anchor_type: str,
        anchor_id: str,
        author: str,
        body: str,
        *,
        parent_id: str | None = None,
    ) -> Comment: ...

    def list(
        self,
        anchor_type: str | None = None,
        anchor_id: str | None = None,
    ) -> List[Comment]: ...

    def get(self, comment_id: str) -> Comment | None: ...
