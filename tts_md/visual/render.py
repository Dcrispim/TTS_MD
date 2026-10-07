from __future__ import annotations

import io
import math
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

from tts_md.audio.ffmpeg import probe_duration, require_ffmpeg
from tts_md.models import VideoConfig
from tts_md.visual.coderender import render_code
from tts_md.visual.cues import ImageCue, PointerCue
from tts_md.visual.timeline import CodeView, PointerView, ScreenState

GRID_FROM = 4
MAX_GROUP = 4
GAP_RATIO = 0.02
CAPTION_WIDTH = 0.9
CAPTION_MARGIN = 0.04
CAPTION_PAD = 0.4
ARROW_SHAPE = (
    (0.0, 0.0), (0.0, 0.78), (0.2, 0.6), (0.34, 0.94),
    (0.46, 0.89), (0.32, 0.56), (0.58, 0.56),
)


@dataclass(frozen=True)
class ImageSource:
    data: Path | bytes
    size: tuple[int, int] | None = None


@dataclass(frozen=True)
class Placement:
    ref: str
    box: tuple[float, float, float, float]
    clip: tuple[int, int, int, int]
    original: tuple[int, int]


@dataclass
class Frame:
    path: Path
    duration: float


@dataclass
class VideoRender:
    path: Path
    frame_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Sprite:
    image: Image.Image
    hotspot: tuple[float, float]


def pointer_to_frame(cue: PointerCue, placement: Placement) -> tuple[float, float]:
    ow, oh = placement.original
    x = cue.x / 100 * ow if cue.x_percent else cue.x
    y = cue.y / 100 * oh if cue.y_percent else cue.y
    bx, by, bw, bh = placement.box
    return bx + x * bw / ow, by + y * bh / oh


def parse_color(value: str) -> tuple[int, int, int, int]:
    color = ImageColor.getrgb(value)
    return color if len(color) == 4 else (*color, 255)


@lru_cache(maxsize=1)
def _caption_font_path() -> str | None:
    fc_match = shutil.which("fc-match")
    if not fc_match:
        return None
    result = subprocess.run(
        [fc_match, "-f", "%{file}", "sans-serif"], capture_output=True, text=True
    )
    path = result.stdout.strip()
    return path if result.returncode == 0 and path and Path(path).is_file() else None


def _caption_font(px: int) -> ImageFont.FreeTypeFont:
    path = _caption_font_path()
    if path:
        return ImageFont.truetype(path, px)
    return ImageFont.load_default(size=px)


def _open_image(data: Path | bytes) -> Image.Image:
    raw = Image.open(io.BytesIO(data) if isinstance(data, bytes) else data)
    raw.load()
    return ImageOps.exif_transpose(raw).convert("RGBA")


def _fit_box(
    size: tuple[int, int], cell: tuple[int, int, int, int], fit: str
) -> tuple[float, float, float, float]:
    cx, cy, cw, ch = cell
    w, h = size
    scale = (max if fit == "cover" else min)(cw / w, ch / h)
    bw, bh = w * scale, h * scale
    return cx + (cw - bw) / 2, cy + (ch - bh) / 2, bw, bh


def _cells(
    n: int, frame: tuple[int, int], top: int = 0
) -> list[tuple[int, int, int, int]]:
    fw, fh = frame
    if n <= 1:
        return [(0, top, fw, fh)]
    gap = max(1, round(GAP_RATIO * min(fw, fh)))
    rows = 1 if n < GRID_FROM else 2
    cols = math.ceil(n / rows)
    cw = (fw - (cols + 1) * gap) // cols
    ch = (fh - (rows + 1) * gap) // rows
    cells = []
    for index in range(n):
        row, col = divmod(index, cols)
        in_row = min(cols, n - row * cols)
        left = (fw - in_row * cw - (in_row - 1) * gap) // 2
        cells.append((left + col * (cw + gap), top + gap + row * (ch + gap), cw, ch))
    return cells


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if current and font.getlength(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _ellipsize(line: str, font: ImageFont.FreeTypeFont, width: float) -> str:
    words = line.split()
    while words and font.getlength(" ".join(words) + "…") > width:
        words.pop()
    return " ".join(words) + "…" if words else "…"


class FrameRenderer:
    def __init__(self, cfg: VideoConfig, images: Mapping[str, ImageSource]) -> None:
        self.cfg = cfg
        self.size = tuple(cfg.size)
        self.sources = images
        self.warnings: list[str] = []
        self._images: dict[str, Image.Image] = {}
        self._codes: dict[tuple, Image.Image] = {}
        self._sprite: _Sprite | None = None
        self._base: Image.Image | None = None

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def image(self, ref: str) -> Image.Image:
        if ref not in self._images:
            if ref not in self.sources:
                raise KeyError(f"image {ref!r} was not resolved")
            self._images[ref] = _open_image(self.sources[ref].data)
        return self._images[ref]

    def original_size(self, ref: str) -> tuple[int, int]:
        size = self.sources[ref].size
        return tuple(size) if size else self.image(ref).size

    def background(self) -> Image.Image:
        if self._base is None:
            base = Image.new("RGBA", self.size, parse_color(self.cfg.background))
            if self.cfg.cover is not None:
                cover = _open_image(self.cfg.cover)
                self._paste(base, cover, (0, 0, *self.size))
            self._base = base
        return self._base

    def layout(self, images: list[ImageCue], position: str = "bottom") -> list[Placement]:
        if len(images) > MAX_GROUP:
            self.warn(f"grupo com {len(images)} imagens (> {MAX_GROUP}); a grade fica apertada")
        top, height = self._content_area(position)
        placements = []
        for cue, cell in zip(images, _cells(len(images), (self.size[0], height), top)):
            box = _fit_box(self.image(cue.ref).size, cell, self.cfg.fit)
            placements.append(Placement(cue.ref, box, cell, self.original_size(cue.ref)))
        return placements

    def render(self, state: ScreenState) -> Image.Image:
        has_content = bool(state.images) or state.code is not None
        if has_content:
            frame = Image.new("RGBA", self.size, parse_color(self.cfg.background))
        else:
            frame = self.background().copy()
        position = self.cfg.captions.position
        if state.code is not None:
            position = "top" if position == "top" else "bottom"
            top, height = self._content_area(position)
            code = self._code(state.code, height)
            frame.alpha_composite(
                code.convert("RGBA"),
                ((self.size[0] - code.width) // 2, top + (height - code.height) // 2),
            )
        else:
            position, placements, point = self._resolve_layout(state)
            for placement in placements:
                self._paste(frame, self.image(placement.ref), placement.clip, placement.box)
            if point is not None:
                self._draw_pointer(frame, point)
        if state.caption:
            self._draw_caption(frame, state.caption, position)
        return frame.convert("RGB")

    def _resolve_layout(
        self, state: ScreenState
    ) -> tuple[str, list[Placement], tuple[float, float] | None]:
        position = self.cfg.captions.position
        first = "top" if position == "top" else "bottom"
        placements = self.layout(state.images, first)
        point = self._pointer_point(state.pointer, placements)
        if position == "auto" and point is not None and point[1] > self.size[1] / 2:
            placements = self.layout(state.images, "top")
            point = self._pointer_point(state.pointer, placements)
            return "top", placements, point
        return first, placements, point

    def _paste(
        self,
        frame: Image.Image,
        image: Image.Image,
        clip: tuple[int, int, int, int],
        box: tuple[float, float, float, float] | None = None,
    ) -> None:
        bx, by, bw, bh = box or _fit_box(image.size, clip, self.cfg.fit)
        cx, cy, cw, ch = clip
        left, top = max(cx, round(bx)), max(cy, round(by))
        right, bottom = min(cx + cw, round(bx + bw)), min(cy + ch, round(by + bh))
        if right <= left or bottom <= top:
            return
        scaled = image.resize((max(1, round(bw)), max(1, round(bh))), Image.LANCZOS)
        ox, oy = left - round(bx), top - round(by)
        frame.alpha_composite(
            scaled.crop((ox, oy, ox + right - left, oy + bottom - top)), (left, top)
        )

    def _content_area(self, position: str) -> tuple[int, int]:
        captions = self.cfg.captions
        fh = self.size[1]
        if not captions.enabled:
            return 0, fh
        line_h = sum(_caption_font(captions.font_px).getmetrics())
        pad = round(captions.font_px * CAPTION_PAD)
        band = line_h * captions.max_lines + 2 * pad + round(fh * CAPTION_MARGIN)
        height = max(fh // 2, fh - band)
        return (fh - height if position == "top" else 0), height

    def _code(self, view: CodeView, height: int) -> Image.Image:
        key = (id(view.cue), view.focus, view.highlight)
        if key not in self._codes:
            result = render_code(view.cue, view, self.cfg, (self.size[0], height))
            for message in result.warnings:
                self.warn(message)
            self._codes[key] = result.image
        return self._codes[key]

    def _pointer_point(
        self, view: PointerView | None, placements: list[Placement]
    ) -> tuple[float, float] | None:
        if view is None:
            return None
        placement = next(
            (p for p in placements if p.ref == view.target.ref), None
        )
        if placement is None:
            self.warn(f"linha {view.cue.line_no}: alvo do ponteiro fora da tela; ignorado")
            return None
        x, y = pointer_to_frame(view.cue, placement)
        cx, cy, cw, ch = placement.clip
        bx, by, bw, bh = placement.box
        if not (max(cx, bx) <= x <= min(cx + cw, bx + bw)
                and max(cy, by) <= y <= min(cy + ch, by + bh)):
            self.warn(
                f"linha {view.cue.line_no}: ponteiro ({view.cue.x:g},{view.cue.y:g}) "
                f"fora da imagem {placement.ref!r}; ignorado"
            )
            return None
        return x, y

    def _draw_pointer(self, frame: Image.Image, point: tuple[float, float]) -> None:
        x, y = point
        sprite = self.sprite()
        frame.alpha_composite(
            sprite.image,
            (round(x - sprite.hotspot[0]), round(y - sprite.hotspot[1])),
        )

    def sprite(self) -> _Sprite:
        if self._sprite is None:
            pointer = self.cfg.pointer
            if pointer.image is None:
                self._sprite = _arrow(pointer.size, parse_color(pointer.color))
            else:
                self._sprite = _custom_sprite(pointer.image, pointer.size, pointer.hotspot)
        return self._sprite

    def _draw_caption(self, frame: Image.Image, text: str, position: str) -> None:
        captions = self.cfg.captions
        fw, fh = self.size
        font = _caption_font(captions.font_px)
        lines = _wrap(text, font, fw * CAPTION_WIDTH)
        if not lines:
            return
        if len(lines) > captions.max_lines:
            preview = text if len(text) <= 40 else text[:40].rstrip() + "…"
            self.warn(
                f"legenda com {len(lines)} linhas (> max_lines {captions.max_lines}); "
                f"cortada: {preview!r}"
            )
            lines = lines[: captions.max_lines]
            lines[-1] = _ellipsize(lines[-1], font, fw * CAPTION_WIDTH)
        ascent, descent = font.getmetrics()
        line_h = ascent + descent
        pad = round(captions.font_px * CAPTION_PAD)
        text_w = max(font.getlength(line) for line in lines)
        box_w = round(text_w) + 2 * pad
        box_h = line_h * len(lines) + 2 * pad
        margin = round(fh * CAPTION_MARGIN)
        top = margin if position == "top" else fh - margin - box_h
        left = (fw - box_w) // 2
        overlay = Image.new("RGBA", self.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rounded_rectangle(
            (left, top, left + box_w, top + box_h), radius=pad,
            fill=parse_color(captions.background),
        )
        for index, line in enumerate(lines):
            x = (fw - font.getlength(line)) / 2
            draw.text((x, top + pad + index * line_h), line, font=font, fill=(255, 255, 255, 255))
        frame.alpha_composite(overlay)


def _arrow(size: int, color: tuple[int, int, int, int]) -> _Sprite:
    scale = 4
    outline = max(1, size // 16) * scale
    side = size * scale
    canvas = Image.new("RGBA", (side + 2 * outline, side + 2 * outline), (0, 0, 0, 0))
    points = [(outline + px * side, outline + py * side) for px, py in ARROW_SHAPE]
    draw = ImageDraw.Draw(canvas)
    draw.polygon(points, fill=color, outline=(255, 255, 255, 255), width=outline)
    image = canvas.resize(
        (canvas.width // scale, canvas.height // scale), Image.LANCZOS
    )
    offset = outline / scale
    return _Sprite(image, (offset, offset))


def _custom_sprite(path: Path, size: int, hotspot: tuple[int, int]) -> _Sprite:
    if path.suffix.lower() == ".svg":
        natural = _svg_size(path)
        scale = size / max(natural)
        target = (max(1, round(natural[0] * scale)), max(1, round(natural[1] * scale)))
        image = _rasterize_svg(path, target)
    else:
        source = _open_image(path)
        natural = source.size
        scale = size / max(natural)
        target = (max(1, round(natural[0] * scale)), max(1, round(natural[1] * scale)))
        image = source.resize(target, Image.LANCZOS)
    return _Sprite(image, (hotspot[0] * scale, hotspot[1] * scale))


def _svg_size(path: Path) -> tuple[int, int]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found. Install ffmpeg to use an SVG pointer.")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        width, height = (int(v) for v in result.stdout.strip().split(",")[:2])
    except ValueError:
        raise RuntimeError(
            f"could not read SVG pointer {path} (ffmpeg needs librsvg): "
            f"{result.stderr.strip()}"
        ) from None
    return width, height


def _rasterize_svg(path: Path, target: tuple[int, int]) -> Image.Image:
    ffmpeg = require_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="tts-md-svg-") as tmp:
        out = Path(tmp) / "pointer.png"
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-width", str(target[0]),
             "-height", str(target[1]), "-keep_ar", "1", "-i", str(path),
             "-frames:v", "1", str(out)],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"could not rasterize SVG pointer {path} (ffmpeg needs librsvg): "
                f"{result.stderr.strip()}"
            )
        return _open_image(out)


def _content_key(state: ScreenState) -> tuple:
    code = state.code
    pointer = state.pointer
    return (
        tuple(cue.ref for cue in state.images),
        (id(code.cue), code.focus, code.highlight) if code else None,
        (id(pointer.cue), pointer.target.ref) if pointer else None,
        state.caption,
    )


def missing_images(states: list[ScreenState], images: Mapping[str, ImageSource]) -> list[str]:
    refs = dict.fromkeys(cue.ref for state in states for cue in state.images)
    return [ref for ref in refs if ref not in images]


def render_frames(
    states: list[ScreenState],
    images: Mapping[str, ImageSource],
    cfg: VideoConfig,
    out_dir: Path,
    total_duration: float | None = None,
) -> tuple[list[Frame], list[str]]:
    missing = missing_images(states, images)
    if missing:
        raise ValueError(f"images not resolved: {', '.join(missing)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    renderer = FrameRenderer(cfg, images)
    timeline = states or [ScreenState(start=0.0, end=total_duration or 0.0)]
    paths: dict[tuple, Path] = {}
    frames: list[Frame] = []
    for index, state in enumerate(timeline):
        key = _content_key(state)
        if key not in paths:
            path = out_dir / f"frame-{len(paths) + 1:04d}.png"
            renderer.render(state).save(path)
            paths[key] = path
        start = 0.0 if index == 0 else state.start
        following = timeline[index + 1].start if index + 1 < len(timeline) else state.end
        frames.append(Frame(paths[key], max(0.0, following - start)))
    if total_duration is not None:
        covered = sum(frame.duration for frame in frames)
        frames[-1].duration += max(0.0, total_duration - covered)
    return frames, renderer.warnings


def _on_grid(frames: list[Frame], step: float) -> list[Frame]:
    entries: list[Frame] = []
    start = 0.0
    for frame in frames:
        end = start + frame.duration
        cuts = [start]
        tick = math.floor(start / step + 1) * step
        while tick < end - 1e-6:
            cuts.append(tick)
            tick += step
        cuts.append(end)
        entries.extend(Frame(frame.path, b - a) for a, b in zip(cuts, cuts[1:]) if b > a)
        start = end
    return entries


def assemble_mp4(
    frames: list[Frame],
    audio: Path,
    output: Path,
    fps: float,
    duration: float | None = None,
) -> Path:
    if not frames:
        raise ValueError("No frames to assemble.")
    ffmpeg = require_ffmpeg()
    total = duration if duration is not None else probe_duration(audio)
    output.parent.mkdir(parents=True, exist_ok=True)
    step = 1 / fps
    shown = sum(frame.duration for frame in frames[:-1])
    timed = [*frames[:-1], Frame(frames[-1].path, max(0.0, total - shown))]
    entries = _on_grid(timed, step) + [Frame(frames[-1].path, step)]
    with tempfile.TemporaryDirectory(prefix="tts-md-concat-") as tmp:
        concat_list = Path(tmp) / "frames.txt"
        with concat_list.open("w", encoding="utf-8") as f:
            for frame in entries:
                escaped = str(frame.path.resolve()).replace("'", "'\\''")
                f.write(f"file '{escaped}'\nduration {frame.duration:.6f}\n")
        result = subprocess.run(
            [
                ffmpeg, "-y", "-v", "error",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0",
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
                "-fps_mode", "vfr",
                "-c:v", "libx264", "-tune", "stillimage", "-bf", "0",
                "-c:a", "aac", "-b:a", "192k",
                "-t", f"{total + step:.6f}",
                "-movflags", "+faststart",
                str(output),
            ],
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to assemble {output}: {result.stderr.strip()}")
    return output


def render_video(
    states: list[ScreenState],
    images: Mapping[str, ImageSource],
    audio: Path,
    output: Path,
    cfg: VideoConfig,
    *,
    work_dir: Path | None = None,
) -> VideoRender:
    total = probe_duration(audio)
    base = work_dir if work_dir is not None else output.parent
    base.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(tempfile.mkdtemp(prefix=".frames-", dir=base))
    try:
        frames, warnings = render_frames(states, images, cfg, frames_dir, total)
        assemble_mp4(frames, audio, output, cfg.fps, total)
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)
    return VideoRender(output, len(frames), warnings)
