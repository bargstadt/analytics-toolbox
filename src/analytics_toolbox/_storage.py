"""Shared storage configuration.

``StorageConfig`` is cross-cutting — every module reads ``storage.connection`` /
``storage.data_dir`` — so it lives here at the package root rather than inside any
one feature module. Kept dependency-light (pydantic core + stdlib) so importing it
never pulls a module's third-party stack.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, field_validator


class StorageConfig(BaseModel):
    """Configuration for where data is stored.

    Paths are expanded (``~`` → home) at construction. A MotherDuck ``md:``
    connection string is left untouched — it is not a filesystem path.
    """

    data_dir: Path
    connection: str

    @field_validator("data_dir")
    @classmethod
    def _expand_data_dir(cls, v: Path) -> Path:
        return Path(v).expanduser()

    @field_validator("connection")
    @classmethod
    def _expand_connection(cls, v: str) -> str:
        if v.startswith("md:"):
            return v
        return str(Path(v).expanduser())
