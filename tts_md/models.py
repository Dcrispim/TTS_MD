from __future__ import annotations

import re
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


_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
MIN_CODE_MAX_LINES = 7


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_positive(name: str, value: Any) -> None:
    if not _is_number(value) or value <= 0:
        raise ValueError(f"video.{name} must be a number > 0, got {value!r}")


def _check_positive_int(name: str, value: Any) -> None:
    if not _is_int(value) or value <= 0:
        raise ValueError(f"video.{name} must be an integer > 0, got {value!r}")


def _check_non_negative(name: str, value: Any) -> None:
    if not _is_number(value) or value < 0:
        raise ValueError(f"video.{name} must be a number >= 0, got {value!r}")


def _check_color(name: str, value: Any) -> None:
    if not isinstance(value, str) or not _COLOR_RE.fullmatch(value):
        raise ValueError(
            f"video.{name} must be a color in #rrggbb or #rrggbbaa, got {value!r}"
        )


def _check_choice(name: str, value: Any, choices: tuple[str, ...]) -> None:
    if value not in choices:
        raise ValueError(
            f"video.{name} must be one of {', '.join(choices)}, got {value!r}"
        )


def _check_int_pair(name: str, value: Any, *, positive: bool) -> None:
    ok = (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(_is_int(v) and (v > 0 if positive else v >= 0) for v in value)
    )
    if not ok:
        kind = "positive" if positive else "non-negative"
        raise ValueError(
            f"video.{name} must be two {kind} integers, got {value!r}"
        )


def _check_existing_file(name: str, value: Path | None) -> None:
    if value is not None and not value.is_file():
        raise FileNotFoundError(f"video.{name} not found: {value}")


def _mapping(name: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping, got {type(value).__name__}")
    return value


def _optional_path(name: str, value: Any, base_dir: Path) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a path string, got {value!r}")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base_dir / path).absolute()


@dataclass
class PointerConfig:
    image: Path | None = None
    size: int = 48
    color: str = "#ff3b30"
    hotspot: tuple[int, int] = (0, 0)

    @classmethod
    def from_dict(cls, data: Any, base_dir: Path = Path(".")) -> PointerConfig:
        data = _mapping("video.pointer", data)
        d = cls()
        hotspot = data.get("hotspot", d.hotspot)
        return cls(
            image=_optional_path("video.pointer.image", data.get("image"), base_dir),
            size=data.get("size", d.size),
            color=data.get("color", d.color),
            hotspot=tuple(hotspot) if isinstance(hotspot, list) else hotspot,
        )

    def validate(self) -> None:
        _check_existing_file("pointer.image", self.image)
        _check_positive_int("pointer.size", self.size)
        _check_color("pointer.color", self.color)
        _check_int_pair("pointer.hotspot", self.hotspot, positive=False)


@dataclass
class CodeConfig:
    theme: str = "monokai"
    font: str | None = None
    min_font_px: int = 14
    highlight: str = "#ffd60a40"
    max_lines: dict[str, int] = field(default_factory=lambda: {"default": 60})

    @classmethod
    def from_dict(cls, data: Any) -> CodeConfig:
        data = _mapping("video.code", data)
        d = cls()
        max_lines = dict(d.max_lines)
        for lang, value in _mapping("video.code.max_lines", data.get("max_lines")).items():
            max_lines[str(lang).lower()] = value
        return cls(
            theme=data.get("theme", d.theme),
            font=data.get("font", d.font),
            min_font_px=data.get("min_font_px", d.min_font_px),
            highlight=data.get("highlight", d.highlight),
            max_lines=max_lines,
        )

    def max_lines_for(self, lang: str | None) -> int:
        key = (lang or "").strip().lower()
        return self.max_lines.get(key, self.max_lines["default"])

    def validate(self) -> None:
        if not isinstance(self.theme, str) or not self.theme:
            raise ValueError(f"video.code.theme must be a style name, got {self.theme!r}")
        _check_positive_int("code.min_font_px", self.min_font_px)
        _check_color("code.highlight", self.highlight)
        for lang, value in self.max_lines.items():
            if not _is_int(value) or value < MIN_CODE_MAX_LINES:
                raise ValueError(
                    f"video.code.max_lines.{lang} must be an integer >= "
                    f"{MIN_CODE_MAX_LINES} (5 lines above the pointed line plus "
                    f"room below), got {value!r}"
                )


@dataclass
class CaptionsConfig:
    enabled: bool = False
    position: str = "auto"
    max_lines: int = 2
    font_px: int = 36
    background: str = "#000000b0"

    @classmethod
    def from_dict(cls, data: Any) -> CaptionsConfig:
        data = _mapping("video.captions", data)
        d = cls()
        return cls(
            enabled=data.get("enabled", d.enabled),
            position=data.get("position", d.position),
            max_lines=data.get("max_lines", d.max_lines),
            font_px=data.get("font_px", d.font_px),
            background=data.get("background", d.background),
        )

    def validate(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError(
                f"video.captions.enabled must be true or false, got {self.enabled!r}"
            )
        _check_choice("captions.position", self.position, ("auto", "top", "bottom"))
        _check_positive_int("captions.max_lines", self.max_lines)
        _check_positive_int("captions.font_px", self.font_px)
        _check_color("captions.background", self.background)


@dataclass
class VideoConfig:
    size: tuple[int, int] = (1920, 1080)
    fps: float = 2
    background: str = "#000000"
    cover: Path | None = None
    fit: str = "contain"
    lead: float = 1.0
    group_gap: float = 1.0
    pointer: PointerConfig = field(default_factory=PointerConfig)
    code: CodeConfig = field(default_factory=CodeConfig)
    captions: CaptionsConfig = field(default_factory=CaptionsConfig)
    upload_max_side: int = 1280
    max_body_mb: float = 50

    @classmethod
    def from_dict(cls, data: Any, base_dir: Path = Path(".")) -> VideoConfig:
        data = _mapping("video", data)
        d = cls()
        size = data.get("size", d.size)
        return cls(
            size=tuple(size) if isinstance(size, list) else size,
            fps=data.get("fps", d.fps),
            background=data.get("background", d.background),
            cover=_optional_path("video.cover", data.get("cover"), base_dir),
            fit=data.get("fit", d.fit),
            lead=data.get("lead", d.lead),
            group_gap=data.get("group_gap", d.group_gap),
            pointer=PointerConfig.from_dict(data.get("pointer"), base_dir),
            code=CodeConfig.from_dict(data.get("code")),
            captions=CaptionsConfig.from_dict(data.get("captions")),
            upload_max_side=data.get("upload_max_side", d.upload_max_side),
            max_body_mb=data.get("max_body_mb", d.max_body_mb),
        )

    def validate(self) -> None:
        _check_int_pair("size", self.size, positive=True)
        _check_positive("fps", self.fps)
        _check_color("background", self.background)
        _check_existing_file("cover", self.cover)
        _check_choice("fit", self.fit, ("contain", "cover"))
        _check_non_negative("lead", self.lead)
        _check_non_negative("group_gap", self.group_gap)
        self.pointer.validate()
        self.code.validate()
        self.captions.validate()
        _check_positive_int("upload_max_side", self.upload_max_side)
        _check_positive("max_body_mb", self.max_body_mb)


@dataclass
class AppConfig:
    default_lang: str
    kokoro: KokoroConfig
    piper: PiperConfig | None = None
    voices: dict[str, VoiceConfig] = field(default_factory=dict)
    video: VideoConfig = field(default_factory=VideoConfig)

    @classmethod
    def default_path(cls) -> Path:
        local = Path("config.yaml")
        if local.exists():
            return local
        return Path(__file__).resolve().parent.parent / "config.example.yaml"

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
            video=VideoConfig.from_dict(data.get("video"), path.resolve().parent),
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

        self.video.validate()

    def get_voice(self, lang: str) -> VoiceConfig:
        if lang in self.voices:
            return self.voices[lang]
        if self.default_lang in self.voices:
            return self.voices[self.default_lang]
        raise KeyError(f"No voice configured for language: {lang}")
