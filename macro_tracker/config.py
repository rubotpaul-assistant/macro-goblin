"""Runtime configuration without credential material."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Non-secret settings and references to runtime-managed secrets."""

    database_path: Path
    spreadsheet_id: str | None
    user_slug: str = "geoff"

    @classmethod
    def from_environment(cls) -> Settings:
        """Load configuration, reusing Iron Goblin's sheet when requested."""
        return cls(
            database_path=Path(os.getenv("MACRO_DATABASE_PATH", "data/macro.sqlite3")),
            spreadsheet_id=os.getenv("MACRO_SPREADSHEET_ID") or os.getenv("GYM_SPREADSHEET_ID"),
            user_slug=os.getenv("MACRO_USER_SLUG", "geoff"),
        )
