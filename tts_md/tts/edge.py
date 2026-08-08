from __future__ import annotations

from pathlib import Path

from tts_md.models import VoiceConfig
from tts_md.tts.base import TTSEngineBase


class EdgeEngine(TTSEngineBase):
    def generate(
        self,
        text: str,
        voice_config: VoiceConfig,
        out_path: Path,
        *,
        speed: float = 1.0,
        lang: str | None = None,
    ) -> Path:
        raise NotImplementedError(
            "Edge TTS is not implemented in the MVP. "
            "Use 'kokoro' or 'piper' engines in config.yaml."
        )
