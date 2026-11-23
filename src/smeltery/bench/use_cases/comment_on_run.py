"""A comment on a run, a candidate of it, or one of its poses. Comments are judgement: they never start compute."""

from __future__ import annotations

import re

#: What a comment may be anchored to: the run, a candidate, or one pose of a candidate.
ANCHOR = re.compile(r"^(run|candidate:[^:]+|pose:[^:]+:\d+)$")


class CommentRefused(ValueError):
    """The comment was not stored: its anchor is not one `ANCHOR` allows, or it has no text."""


class CommentOnRun:
    def __init__(self, comment_store, identity, clock):
        self.comment_store = comment_store
        self.identity = identity
        self.clock = clock

    @staticmethod
    def check(anchor: str, body: str) -> None:
        """`CommentRefused` naming what is wrong with a comment, before anything is looked up or stored."""
        if not ANCHOR.match(anchor):
            raise CommentRefused("anchor is 'run', 'candidate:<name>' or 'pose:<name>:<i>'")
        if not body.strip():
            raise CommentRefused("a comment needs some text")

    def run(self, digest: str, anchor: str, body: str):
        """Store a comment on the run whose input digest is `digest`, by `Identity.current()`; returns the stored
        `Comment` (the store stamps its time)."""
        self.check(anchor, body)
        return self.comment_store.add(run=digest, anchor=anchor, author=self.identity.current(), body=body)
