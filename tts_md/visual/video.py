from __future__ import annotations

import base64
import binascii
import io
from dataclasses import dataclass, field, replace
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from tts_md.audio.ffmpeg import probe_duration
from tts_md.models import VideoConfig
from tts_md.visual.cues import CueExtraction, CueWarning, ImageCue
from tts_md.visual.render import ImageSource, render_video
from tts_md.visual.timeline import ScreenState, build_timeline

MAX_UPLOAD_PIXELS = 16_000_000
MAX_REQUEST_PIXELS = 64_000_000
MAX_SIZE_SIDE = 100_000
UPLOAD_FORMATS = ("PNG", "JPEG", "GIF", "WEBP", "BMP", "TIFF")


@dataclass
class Resolution:
    images: dict[str, ImageSource] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)


class FileSystemResolver:
    def __init__(self, base: Path) -> None:
        self.base = base

    def path_for(self, ref: str) -> Path:
        path = Path(ref).expanduser()
        return path if path.is_absolute() else self.base / path

    def resolve(self, refs: list[str]) -> Resolution:
        result = Resolution()
        for ref in dict.fromkeys(refs):
            path = self.path_for(ref)
            if not path.exists():
                result.missing[ref] = f"not found: {path}"
                continue
            if not path.is_file():
                result.missing[ref] = f"not a file: {path}"
                continue
            try:
                with Image.open(path) as image:
                    image.verify()
            except (OSError, UnidentifiedImageError):
                result.missing[ref] = f"not a readable image: {path}"
                continue
            result.images[ref] = ImageSource(path)
        return result


class UploadResolver:
    def __init__(self, uploads: object) -> None:
        self.uploads = uploads if isinstance(uploads, dict) else {}

    def resolve(self, refs: list[str]) -> Resolution:
        result = Resolution()
        total = 0
        for ref in dict.fromkeys(refs):
            if ref not in self.uploads:
                result.missing[ref] = "not uploaded"
                continue
            try:
                source, pixels = _decode_upload(self.uploads[ref], MAX_REQUEST_PIXELS - total)
            except ValueError as exc:
                result.missing[ref] = str(exc)
                continue
            total += pixels
            result.images[ref] = source
        return result


def _decode_upload(entry: object, budget: int) -> tuple[ImageSource, int]:
    if not isinstance(entry, dict) or not isinstance(entry.get("data"), str):
        raise ValueError("upload must be an object with a base64 'data' string")
    try:
        data = base64.b64decode(entry["data"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 in 'data'") from exc
    size = entry.get("size")
    if size is not None:
        if (
            not isinstance(size, list)
            or len(size) != 2
            or not all(
                isinstance(v, int) and not isinstance(v, bool) and 0 < v <= MAX_SIZE_SIDE
                for v in size
            )
        ):
            raise ValueError(
                f"'size' must be [width, height] with integers between 1 and {MAX_SIZE_SIDE}"
            )
        size = (size[0], size[1])
    try:
        with Image.open(io.BytesIO(data), formats=UPLOAD_FORMATS) as image:
            width, height = image.size
            pixels = width * height
            if pixels > MAX_UPLOAD_PIXELS:
                raise ValueError(
                    f"image is {width}x{height} ({pixels:,} px), "
                    f"above the {MAX_UPLOAD_PIXELS:,} px per-image limit"
                )
            if pixels > budget:
                raise ValueError(
                    f"image is {width}x{height} ({pixels:,} px) and the request "
                    f"would exceed the {MAX_REQUEST_PIXELS:,} px total limit"
                )
            image.verify()
    except Image.DecompressionBombError as exc:
        raise ValueError(f"image above the {MAX_UPLOAD_PIXELS:,} px per-image limit") from exc
    except (OSError, SyntaxError, UnidentifiedImageError) as exc:
        raise ValueError(
            f"not a readable image (accepted formats: {', '.join(UPLOAD_FORMATS)})"
        ) from exc
    return ImageSource(data, size), pixels


def encode_upload(source: ImageSource, max_side: int) -> dict:
    raw = source.data if isinstance(source.data, Path) else io.BytesIO(source.data)
    with Image.open(raw) as opened:
        image = ImageOps.exif_transpose(opened)
        size = source.size or image.size
        if max(image.size) > max_side:
            if image.mode not in ("L", "LA", "RGB", "RGBA"):
                image = image.convert("RGBA")
            scale = max_side / max(image.size)
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.LANCZOS,
            )
        if image.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
            image = image.convert("RGBA")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    return {"data": base64.b64encode(buffer.getvalue()).decode("ascii"), "size": list(size)}


def image_refs(extraction: CueExtraction) -> list[str]:
    return list(dict.fromkeys(c.ref for c in extraction.cues if isinstance(c, ImageCue)))


def format_warning(warning: CueWarning | str) -> str:
    if isinstance(warning, CueWarning):
        return f"line {warning.line_no}: {warning.message}"
    return warning


def _merge(target: list[str], items: list[CueWarning] | list[str]) -> None:
    for item in items:
        text = format_warning(item)
        if text not in target:
            target.append(text)


def render_document(
    extraction: CueExtraction,
    spans: dict[int, tuple[float, float]],
    audio: Path,
    output: Path,
    cfg: VideoConfig,
    images: dict[str, ImageSource],
    *,
    work_dir: Path | None = None,
) -> list[str]:
    total = probe_duration(audio)
    lines = extraction.markdown.splitlines()
    timeline = build_timeline(extraction.cues, spans, lines, cfg, total)
    result = render_video(timeline.states, images, audio, output, cfg, work_dir=work_dir)
    warnings: list[str] = []
    _merge(warnings, timeline.warnings)
    _merge(warnings, result.warnings)
    return warnings


def segment(states: list[ScreenState], start: float, end: float) -> list[ScreenState]:
    cut = []
    for state in states:
        if state.end <= start or state.start >= end:
            continue
        cut.append(
            replace(state, start=max(state.start, start) - start, end=min(state.end, end) - start)
        )
    return cut


class StreamVideo:
    def __init__(
        self,
        extraction: CueExtraction,
        cfg: VideoConfig,
        images: dict[str, ImageSource],
        *,
        work_dir: Path | None = None,
    ) -> None:
        self.extraction = extraction
        self.lines = extraction.markdown.splitlines()
        self.cfg = cfg
        self.images = images
        self.work_dir = work_dir
        self.spans: dict[int, tuple[float, float]] = {}
        self.cursor = 0.0
        self.render_warnings: list[str] = []

    def add(self, audio: Path, line_no: int, output: Path) -> Path:
        start = self.cursor
        end = start + probe_duration(audio)
        self.spans[line_no] = (start, end)
        self.cursor = end
        timeline = build_timeline(self.extraction.cues, self.spans, self.lines, self.cfg, end)
        states = segment(timeline.states, start, end)
        result = render_video(states, self.images, audio, output, self.cfg, work_dir=self.work_dir)
        _merge(self.render_warnings, result.warnings)
        return result.path

    def warnings(self) -> list[str]:
        timeline = build_timeline(
            self.extraction.cues, self.spans, self.lines, self.cfg, self.cursor
        )
        warnings: list[str] = []
        _merge(warnings, timeline.warnings)
        _merge(warnings, self.render_warnings)
        return warnings
