from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SpeechBlock:
    text: str
    lang: str = "pt-BR"
    speak: bool = True
    voice: str | None = None
    speed: float = 1.0
    pause_after: float = 0.0
    # Linha do Markdown que originou o bloco: agrupa os blocos de uma
    # mesma linha numa faixa unica no modo --stream.
    line_no: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StreamTrack:
    index: int
    path: Path
    text: str
    lang: str


@dataclass
class VoiceConfig:
    engine: str
    model: str | None = None
    voice: str | None = None
    speed: float = 1.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VoiceConfig:
        return cls(
            engine=data["engine"],
            model=data.get("model"),
            voice=data.get("voice"),
            speed=float(data.get("speed", 1.0)),
        )


@dataclass
class KokoroConfig:
    model: str
    voices_bin: str
    models_dir: Path

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KokoroConfig:
        return cls(
            model=data["model"],
            voices_bin=data["voices_bin"],
            models_dir=Path(data["models_dir"]),
        )

    @property
    def model_path(self) -> Path:
        return self.models_dir / self.model

    @property
    def voices_bin_path(self) -> Path:
        return self.models_dir / self.voices_bin


@dataclass
class PiperConfig:
    models_dir: Path

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiperConfig:
        return cls(models_dir=Path(data["models_dir"]))


@dataclass
class AppConfig:
    default_lang: str
    kokoro: KokoroConfig
    piper: PiperConfig | None = None
    voices: dict[str, VoiceConfig] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)

        voices = {
            lang: VoiceConfig.from_dict(cfg)
            for lang, cfg in data.get("voices", {}).items()
        }

        piper_data = data.get("piper")

        return cls(
            default_lang=data.get("default_lang", "pt-BR"),
            kokoro=KokoroConfig.from_dict(data["kokoro"]),
            piper=PiperConfig.from_dict(piper_data) if piper_data else None,
            voices=voices,
        )

    def validate(self) -> None:
        if not self.kokoro.model_path.exists():
            raise FileNotFoundError(
                f"Kokoro model not found: {self.kokoro.model_path}"
            )
        if not self.kokoro.voices_bin_path.exists():
            raise FileNotFoundError(
                f"Kokoro voices bin not found: {self.kokoro.voices_bin_path}"
            )
        if self.piper is not None and not self.piper.models_dir.exists():
            raise FileNotFoundError(
                f"Piper models directory not found: {self.piper.models_dir}"
            )

        for lang, voice in self.voices.items():
            if voice.engine == "piper":
                if self.piper is None:
                    raise ValueError(
                        f"Voice for {lang} uses Piper, but no 'piper' section is configured"
                    )
                if not voice.model:
                    raise ValueError(f"Piper voice for {lang} requires 'model'")
                model_path = self.piper.models_dir / voice.model
                if not model_path.exists():
                    raise FileNotFoundError(
                        f"Piper model not found for {lang}: {model_path}"
                    )
            elif voice.engine == "kokoro":
                if not voice.voice:
                    raise ValueError(f"Kokoro voice for {lang} requires 'voice'")
            elif voice.engine == "edge":
                pass
            else:
                raise ValueError(f"Unknown engine '{voice.engine}' for {lang}")

    def get_voice(self, lang: str) -> VoiceConfig:
        if lang in self.voices:
            return self.voices[lang]
        if self.default_lang in self.voices:
            return self.voices[self.default_lang]
        raise KeyError(f"No voice configured for language: {lang}")
