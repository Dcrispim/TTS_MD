from __future__ import annotations

from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro

from tts_md.models import AppConfig, VoiceConfig
from tts_md.tts.base import TTSEngineBase

TARGET_SAMPLE_RATE = 22050

# Kokoro fonemiza via espeak-ng, que usa codigos proprios.
ESPEAK_LANGS = {
    "en-us": "en-us",
    "en-gb": "en-gb",
    "pt-br": "pt-br",
    "pt-pt": "pt",
    "pt": "pt-br",
    "es": "es",
    "es-es": "es",
    "fr": "fr-fr",
    "fr-fr": "fr-fr",
    "hi": "hi",
    "it": "it",
    "ja": "ja",
    "ja-jp": "ja",
    "zh": "cmn",
    "zh-cn": "cmn",
}
DEFAULT_ESPEAK_LANG = "en-us"


def espeak_lang(lang: str | None) -> str:
    if not lang:
        return DEFAULT_ESPEAK_LANG
    return ESPEAK_LANGS.get(lang.lower(), DEFAULT_ESPEAK_LANG)


class KokoroEngine(TTSEngineBase):
    def __init__(self, config: AppConfig):
        self.config = config
        self._kokoro: Kokoro | None = None

    @property
    def kokoro(self) -> Kokoro:
        if self._kokoro is None:
            self._kokoro = Kokoro(
                str(self.config.kokoro.model_path),
                str(self.config.kokoro.voices_bin_path),
            )
        return self._kokoro

    def generate(
        self,
        text: str,
        voice_config: VoiceConfig,
        out_path: Path,
        *,
        speed: float = 1.0,
        lang: str | None = None,
    ) -> Path:
        voice = voice_config.voice or "af_heart"
        effective_speed = speed * voice_config.speed
        samples, sample_rate = self.kokoro.create(
            text,
            voice=voice,
            speed=effective_speed,
            lang=espeak_lang(lang),
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), samples, sample_rate)

        if sample_rate != TARGET_SAMPLE_RATE:
            normalized = out_path.with_suffix(".norm.wav")
            from tts_md.audio.ffmpeg import normalize_sample_rate

            normalize_sample_rate(out_path, normalized, TARGET_SAMPLE_RATE)
            out_path.unlink(missing_ok=True)
            normalized.rename(out_path)

        return out_path
