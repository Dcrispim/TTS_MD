from __future__ import annotations

from pathlib import Path

import wave
from piper import PiperVoice, SynthesisConfig

from tts_md.models import AppConfig, VoiceConfig
from tts_md.tts.base import TTSEngineBase

TARGET_SAMPLE_RATE = 22050


class PiperEngine(TTSEngineBase):
    def __init__(self, config: AppConfig):
        self.config = config
        self._voices: dict[str, PiperVoice] = {}

    def _get_voice(self, model_name: str) -> PiperVoice:
        if self.config.piper is None:
            raise ValueError("Piper engine requires a 'piper' section in config.yaml")
        if model_name not in self._voices:
            model_path = self.config.piper.models_dir / model_name
            self._voices[model_name] = PiperVoice.load(str(model_path))
        return self._voices[model_name]

    def generate(
        self,
        text: str,
        voice_config: VoiceConfig,
        out_path: Path,
        *,
        speed: float = 1.0,
        lang: str | None = None,
    ) -> Path:
        if not voice_config.model:
            raise ValueError("Piper voice config requires 'model'")

        voice = self._get_voice(voice_config.model)
        effective_speed = speed * voice_config.speed
        length_scale = 1.0 / effective_speed if effective_speed > 0 else 1.0

        syn_config = SynthesisConfig(length_scale=length_scale)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with wave.open(str(out_path), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file, syn_config=syn_config)

        return out_path
