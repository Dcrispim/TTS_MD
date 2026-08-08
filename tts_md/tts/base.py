from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from tts_md.models import VoiceConfig


class TTSEngineBase(ABC):
    @abstractmethod
    def generate(
        self,
        text: str,
        voice_config: VoiceConfig,
        out_path: Path,
        *,
        speed: float = 1.0,
        lang: str | None = None,
    ) -> Path:
        ...
