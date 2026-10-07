from __future__ import annotations

import io
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont
from pygments.formatters.img import STYLES, FontManager, FontNotFound, ImageFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.styles import get_style_by_name
from pygments.token import Token
from pygments.util import ClassNotFound

from tts_md.models import VideoConfig
from tts_md.visual.codewin import code_window
from tts_md.visual.cues import CodeCue
from tts_md.visual.timeline import CodeView

FIT_RATIO = 0.95
MAX_FONT_PX = 32
MIN_PX = 4
PROBE_PX = 100
IMAGE_PAD = 10
LINE_PAD = 1
NUMBER_PAD = 6
TAB_SIZE = 4
DEFAULT_ALPHA = 0x40
FALLBACK_THEME = "default"
ELLIPSIS = "…"
LINE_SPLIT_RE = re.compile(r"(?<=\n)")


@dataclass
class CodeRender:
    image: Image.Image
    first: int
    last: int
    font_px: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Theme:
    name: str
    background: str
    number_fg: str
    number_bg: str
    mark_fg: tuple[int, int, int]


@dataclass
class _Layout:
    px: int
    font: ImageFont.FreeTypeFont
    line_h: int
    number_w: int
    advance: float

    @property
    def code_x(self) -> int:
        return IMAGE_PAD + self.number_w - NUMBER_PAD + 1

    @property
    def text_x(self) -> int:
        return IMAGE_PAD + self.number_w

    def height(self, rows: int) -> int:
        return 2 * IMAGE_PAD + rows * self.line_h

    def mark_geometry(self) -> tuple[int, int, int]:
        r = max(1, self.line_h // 10)
        step = r * 3
        return r, step, self.code_x + NUMBER_PAD + 2 * step + 2 * r + self.line_h // 2

    def mark_width(self, text: str) -> int:
        return int(self.mark_geometry()[2] + self.font.getlength(text)) + IMAGE_PAD

    def code_width(self, line: str) -> int:
        return int(self.text_x + self.font.getlength(line)) + IMAGE_PAD + 1


@lru_cache(maxsize=1)
def _system_monospace() -> str | None:
    if shutil.which("fc-match") is None:
        return None
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{family[0]}", "monospace"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


@lru_cache(maxsize=16)
def _font_paths(name: str) -> dict[str, str] | None:
    try:
        manager = FontManager(name, PROBE_PX)
    except (FontNotFound, OSError):
        return None
    return {style: manager.fonts[style].path for style in STYLES}


def _resolve_font(configured: str | None, warnings: list[str]) -> dict[str, str]:
    if configured:
        paths = _font_paths(configured)
        if paths:
            return paths
        warnings.append(f"fonte {configured!r} não encontrada; usando a monoespaçada do sistema")
    system = _system_monospace()
    if system is None:
        warnings.append("fc-match indisponível; usando a fonte padrão do Pygments")
    elif paths := _font_paths(system):
        return paths
    else:
        warnings.append(f"fonte do sistema {system!r} não encontrada; usando a fonte padrão do Pygments")
    paths = _font_paths("")
    if paths:
        return paths
    raise RuntimeError("nenhuma fonte monoespaçada utilizável encontrada para renderizar código")


@lru_cache(maxsize=256)
def _truetype(path: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, px)


def _layout(path: str, px: int, number_chars: int) -> _Layout:
    font = _truetype(path, px)
    fontw, fonth = font.getbbox("M")[2:4]
    return _Layout(px, font, fonth + LINE_PAD, fontw * number_chars + 2 * NUMBER_PAD, font.getlength("M"))


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _premix(color: str, background: str) -> str:
    value = color.lstrip("#")
    alpha = (int(value[6:8], 16) if len(value) == 8 else DEFAULT_ALPHA) / 255
    mixed = (round(b + (c - b) * alpha) for c, b in zip(_hex_rgb(value[:6]), _hex_rgb(background)))
    return "#" + "".join(f"{v:02x}" for v in mixed)


def _load_theme(name: str, warnings: list[str]):
    try:
        style = get_style_by_name(name)
    except ClassNotFound:
        warnings.append(f"tema de código {name!r} não existe; usando {FALLBACK_THEME!r}")
        name = FALLBACK_THEME
        style = get_style_by_name(name)
    background = style.background_color or "#ffffff"
    text = style.style_for_token(Token)["color"]
    text = f"#{text}" if text else "#000000"
    number_fg = style.line_number_color
    number_fg = text if number_fg in (None, "", "inherit") else number_fg
    number_bg = style.line_number_background_color
    number_bg = background if number_bg in (None, "", "inherit", "transparent") else number_bg
    bg_rgb, fg_rgb = _hex_rgb(background), _hex_rgb(number_fg)
    mark_fg = tuple(round(b + (f - b) * 0.6) for b, f in zip(bg_rgb, fg_rgb))
    return style, _Theme(name, background, number_fg, number_bg, mark_fg)


def _lexer(lang: str):
    options = {"stripnl": False, "ensurenl": True}
    try:
        return get_lexer_by_name(lang.strip(), **options) if lang.strip() else TextLexer(**options)
    except ClassNotFound:
        return TextLexer(**options)


def _window_tokens(tokens: list, first: int, last: int, long_lines: set[int], limit: int) -> Iterator:
    line, col, cut = 1, 0, False
    for ttype, value in tokens:
        for part in LINE_SPLIT_RE.split(value):
            if not part:
                continue
            newline = part.endswith("\n")
            text = part[:-1] if newline else part
            if first <= line <= last:
                if line in long_lines:
                    if limit - col > 0:
                        yield ttype, text[:limit - col]
                    if not cut and col + len(text) >= limit:
                        yield ttype, ELLIPSIS
                        cut = True
                elif text:
                    yield ttype, text
                col += len(text)
                if newline:
                    yield ttype, "\n"
            if newline:
                line, col, cut = line + 1, 0, False
                if line > last:
                    return


def _fit_px(path, number_chars, start, floor, fits) -> _Layout:
    layout = _layout(path, start, number_chars)
    while layout.px > floor and not fits(layout):
        layout = _layout(path, layout.px - 1, number_chars)
    return layout


def _draw_mark(draw, layout: _Layout, y: int, text: str, color) -> None:
    r, step, text_x = layout.mark_geometry()
    cy = y + layout.line_h // 2
    x = layout.code_x + NUMBER_PAD
    for i in range(3):
        cx = x + r + i * step
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=color)
    draw.text((text_x, cy), text, font=layout.font, fill=color, anchor="lm")


def _lines_label(count: int) -> str:
    return f"{count} linha" if count == 1 else f"{count} linhas"


def _can_override_fonts(fonts) -> bool:
    return hasattr(fonts, "variable") and isinstance(getattr(fonts, "fonts", None), dict)


def render_code(
    cue: CodeCue, view: CodeView, cfg: VideoConfig, frame_size: tuple[int, int]
) -> CodeRender:
    warnings: list[str] = []
    lines = [line.expandtabs(TAB_SIZE) for line in cue.lines]
    n = len(lines)
    x = cfg.code.max_lines_for(cue.lang)
    window = code_window(n, x, view.focus)
    first, last = window.first, window.last
    lang_label = cue.lang or "text"
    if window.truncated_focus:
        warnings.append(
            f"{lang_label}: intervalo L{view.focus[0]}-{view.focus[1]} maior que a janela de "
            f"{x} linhas; mostrando L{first}-{last}"
        )

    paths = _resolve_font(cfg.code.font, warnings)
    style, theme = _load_theme(cfg.code.theme, warnings)
    visible = last - first + 1
    shown = lines[first - 1:last]
    longest = max(shown, key=len, default="")
    top_mark = f"+{_lines_label(first - 1)}" if first > 1 else ""
    bottom_mark = f"+{_lines_label(n - last)}" if last < n else ""
    marks = [t for t in (top_mark, bottom_mark) if t]
    rows = max(1, visible + len(marks))
    number_chars = len(str(max(last, 1)))
    max_w, max_h = frame_size[0] * FIT_RATIO, frame_size[1] * FIT_RATIO
    normal = paths["NORMAL"]

    probe = _layout(normal, PROBE_PX, number_chars)
    by_h = (max_h - 2 * IMAGE_PAD - rows * LINE_PAD) * PROBE_PX / (rows * (probe.line_h - LINE_PAD))
    start = max(MIN_PX, min(MAX_FONT_PX, int(by_h)))
    layout = _fit_px(normal, number_chars, start, MIN_PX, lambda lo: lo.height(rows) <= max_h)
    height_px = layout.px

    def width_of(lo: _Layout, line: str) -> int:
        return max([lo.code_width(line)] + [lo.mark_width(t) for t in marks])

    if width_of(layout, longest) > max_w:
        floor = min(layout.px, cfg.code.min_font_px)
        layout = _fit_px(normal, number_chars, layout.px, floor, lambda lo: width_of(lo, longest) <= max_w)

    def cut_plan(lo: _Layout) -> tuple[set[int], int]:
        if width_of(lo, longest) <= max_w:
            return set(), 0
        limit = max(1, int((max_w - lo.text_x - IMAGE_PAD - 1) / lo.advance) - 1)
        return {first + i for i, line in enumerate(shown) if len(line) > limit + 1}, limit

    tokens = list(_lexer(cue.lang or "").get_tokens("\n".join(lines) + "\n"))
    hl_lines: list[int] = []
    if view.highlight is not None:
        lo_hl, hi_hl = max(view.highlight[0], first), min(view.highlight[1], last)
        hl_lines = list(range(lo_hl - first + 1, hi_hl - first + 2))
    hl_color = _premix(cfg.code.highlight, theme.background)

    override_warned: list[bool] = []

    def render(lo: _Layout) -> tuple[Image.Image, set[int], int]:
        long_lines, limit = cut_plan(lo)
        formatter = ImageFormatter(
            style=style, font_name=normal, font_size=lo.px,
            line_numbers=True, line_number_start=first, line_number_chars=number_chars,
            line_number_fg=theme.number_fg, line_number_bg=theme.number_bg,
            image_pad=IMAGE_PAD, line_pad=LINE_PAD, line_number_pad=NUMBER_PAD,
            hl_lines=hl_lines, hl_color=hl_color,
        )
        if _can_override_fonts(formatter.fonts):
            formatter.fonts.variable = False
            formatter.fonts.fonts = {s: _truetype(p, lo.px) for s, p in paths.items()}
        elif not override_warned:
            override_warned.append(True)
            warnings.append("Pygments sem FontManager.variable/fonts; usando a resolução de fonte pública")
        buf = io.BytesIO()
        formatter.format(_window_tokens(tokens, first, last, long_lines, limit), buf)
        buf.seek(0)
        code = Image.open(buf).convert("RGB")
        top = lo.line_h if top_mark else 0
        bottom = lo.line_h if bottom_mark else 0
        width = max([code.width] + [lo.mark_width(t) for t in marks])
        out = Image.new("RGB", (width, code.height + top + bottom), theme.background)
        out.paste(code, (0, top))
        draw = ImageDraw.Draw(out)
        if width > code.width:
            for row in hl_lines:
                y = top + IMAGE_PAD + (row - 1) * lo.line_h
                draw.rectangle([(code.width, y), (width, y + lo.line_h)], fill=hl_color)
        if top_mark:
            _draw_mark(draw, lo, 0, top_mark, theme.mark_fg)
        if bottom_mark:
            _draw_mark(draw, lo, top + code.height, bottom_mark, theme.mark_fg)
        return out, long_lines, limit

    image, long_lines, limit = render(layout)
    if (image.width > max_w or image.height > max_h) and layout.px > MIN_PX:
        layout = _layout(normal, layout.px - 1, number_chars)
        image, long_lines, limit = render(layout)

    if height_px < cfg.code.min_font_px:
        hint = "considere reduzir max_lines" if visible > 1 else "o quadro é baixo demais"
        warnings.append(
            f"{lang_label}: {_lines_label(visible)} → fonte {height_px}px "
            f"(< min {cfg.code.min_font_px}px); {hint}"
        )
    if long_lines:
        warnings.append(f"{lang_label}: {len(long_lines)} linha(s) cortada(s) em {limit + 1} colunas")
    if image.width > max_w or image.height > max_h:
        scale = min(max_w / image.width, max_h / image.height)
        size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        warnings.append(
            f"{lang_label}: imagem {image.width}x{image.height} reduzida para "
            f"{size[0]}x{size[1]} para caber no quadro {frame_size[0]}x{frame_size[1]}"
        )
        image = image.resize(size, Image.LANCZOS)
    if image.width > frame_size[0] or image.height > frame_size[1]:
        raise AssertionError(f"render_code: {image.size} excede {frame_size}")
    return CodeRender(image=image, first=first, last=last, font_px=layout.px, warnings=warnings)
