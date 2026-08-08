from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from types import TracebackType

MAX_SLUG_LENGTH = 40


def slugify(text: str, *, max_length: int = MAX_SLUG_LENGTH) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug


def track_name(index: int, text: str, *, width: int = 3) -> str:
    slug = slugify(text)
    prefix = f"{index:0{width}d}"
    return f"{prefix}-{slug}" if slug else prefix


class M3UWriter:
    """Playlist gravada faixa a faixa, para tocar enquanto o resto e sintetizado."""

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def __enter__(self) -> M3UWriter:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")
        self._handle.write("#EXTM3U\n")
        self._handle.flush()
        return self

    def add(self, audio_path: Path, title: str) -> None:
        if self._handle is None:
            raise RuntimeError("M3UWriter must be used as a context manager")
        # Caminho relativo: a pasta continua tocavel se for movida.
        self._handle.write(f"#EXTINF:-1,{title}\n{audio_path.name}\n")
        self._handle.flush()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
