from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def publish_directory(
    output_directory: str | Path,
    writer: Callable[[Path], None],
    *,
    overwrite: bool = False,
) -> None:
    output = Path(output_directory)
    if output.exists() and not overwrite:
        raise ValueError(f"output directory already exists: {output.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    temporary = output.with_name(f".{output.name}.{token}.tmp")
    backup = output.with_name(f".{output.name}.{token}.backup")
    temporary.mkdir()
    published = False
    try:
        writer(temporary)
        if output.exists():
            if not overwrite:
                raise ValueError(f"output directory already exists: {output.name}")
            os.replace(output, backup)
        try:
            os.replace(temporary, output)
            published = True
        except Exception:
            if backup.exists() and not output.exists():
                os.replace(backup, output)
            raise
        if backup.exists():
            _remove_path(backup)
    finally:
        if temporary.exists():
            _remove_path(temporary)
        if backup.exists() and not published:
            if not output.exists():
                os.replace(backup, output)
            else:
                _remove_path(backup)
