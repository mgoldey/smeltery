"""The ligands step's routes. Empty scaffold: Task 4.2 adds `POST /api/pipelines/ligands` here.

Its own module so the tasks building step views never edit the same file.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/pipelines", tags=["ligands"])
