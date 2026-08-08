from __future__ import annotations

from pathlib import Path

from tts_md.models import AppConfig, SpeechBlock, VoiceConfig
from tts_md.tts.base import TTSEngineBase
from tts_md.tts.edge import EdgeEngine
from tts_md.tts.kokoro import KokoroEngine
from tts_md.tts.piper import PiperEngine


class TTSRouter:
    def __init__(self, config: AppConfig):
        self.config = config
        self._engines: dict[str, TTSEngineBase] = {}

    def get_engine(self, engine_name: str) -> TTSEngineBase:
        if engine_name not in self._engines:
            if engine_name == "kokoro":
                self._engines[engine_name] = KokoroEngine(self.config)
            elif engine_name == "piper":
                self._engines[engine_name] = PiperEngine(self.config)
            elif engine_name == "edge":
                self._engines[engine_name] = EdgeEngine()
            else:
                raise ValueError(f"Unknown TTS engine: {engine_name}")
        return self._engines[engine_name]

    def resolve_voice(self, block: SpeechBlock) -> VoiceConfig:
        voice_cfg = self.config.get_voice(block.lang)
        if block.voice:
            return VoiceConfig(
                engine=voice_cfg.engine,
                model=voice_cfg.model,
                voice=block.voice,
                speed=block.speed or voice_cfg.speed,
            )
        return VoiceConfig(
            engine=voice_cfg.engine,
            model=voice_cfg.model,
            voice=voice_cfg.voice,
            speed=block.speed or voice_cfg.speed,
        )

    def synthesize(self, block: SpeechBlock, out_path: Path) -> Path:
        voice_cfg = self.resolve_voice(block)
        engine = self.get_engine(voice_cfg.engine)
        return engine.generate(
            block.text,
            voice_cfg,
            out_path,
            speed=block.speed,
            lang=block.lang,
        )
