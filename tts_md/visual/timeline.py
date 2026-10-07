from __future__ import annotations

import math
import re
import textwrap
from bisect import bisect_left
from dataclasses import dataclass, field

from tts_md.models import VideoConfig
from tts_md.visual.cues import (
    ClearCue,
    CodeCue,
    Cue,
    CueWarning,
    ImageCue,
    LinePointerCue,
    PointerCue,
)

HEADING_RE = re.compile(r"^\s*#{1,6}\s")
HEADING_PREFIX_RE = re.compile(r"^\s*#{1,6}\s+")
CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
EMPHASIS_RES = (
    re.compile(r"(?<![^\W_]|\*)(\*{1,3})([^*\s](?:[^*]*?[^*\s])?)\1(?![^\W_]|\*)"),
    re.compile(r"(?<!\w)(_{1,3})([^_\s](?:[^_]*?[^_\s])?)\1(?!\w)"),
)
PLACEHOLDER_RE = re.compile("\x00(\\d+)\x00")

HEADING_RANK = 0
ATTRIBUTED_RANK = 1
OWN_LINE_RANK = 2
MIN_VISIBLE = 0.5


@dataclass
class CodeView:
    cue: CodeCue
    focus: tuple[int, int] | None = None
    highlight: tuple[int, int] | None = None


@dataclass
class PointerView:
    cue: PointerCue
    target: ImageCue


@dataclass
class ScreenState:
    start: float
    end: float
    images: list[ImageCue] = field(default_factory=list)
    code: CodeView | None = None
    pointer: PointerView | None = None
    caption: str | None = None

    def same_content(self, other: ScreenState) -> bool:
        return (
            self.images == other.images
            and self.code == other.code
            and self.pointer == other.pointer
            and self.caption == other.caption
        )


@dataclass
class Timeline:
    states: list[ScreenState] = field(default_factory=list)
    warnings: list[CueWarning] = field(default_factory=list)


@dataclass
class _Event:
    key: tuple[int, int, float, int]
    time: float
    anchor: float
    cue: Cue | None
    screen: _Screen | None = None
    seen: int = 0


@dataclass
class _Screen:
    start: float
    anchor: float
    line_no: int
    images: list[ImageCue] = field(default_factory=list)
    code: CodeCue | None = None
    last_anchor: float = 0.0
    duration: float | None = None
    stop: float = math.inf
    end: float = 0.0
    first_lines: tuple[int, int] | None = None


@dataclass
class _Mark:
    start: float
    end: float
    screen: _Screen
    line_no: int
    pointer: PointerView | None = None
    lines: tuple[int, int] | None = None


@dataclass
class _Caption:
    start: float
    end: float
    text: str


def build_timeline(
    cues: list[Cue],
    line_spans: dict[int, tuple[float, float]],
    lines: list[str],
    cfg: VideoConfig,
    total_duration: float,
    caption_chars_per_line: int | None = None,
) -> Timeline:
    timeline = Timeline()
    total = max(0.0, total_duration)
    events = _events(cues, line_spans, lines, cfg, total, timeline.warnings)
    screens = _screens(events, total, cfg.group_gap, timeline.warnings)
    marks = _marks(events, timeline.warnings)

    captions: list[_Caption] = []
    if cfg.captions.enabled:
        width = caption_chars_per_line or _default_chars_per_line(cfg)
        captions = _captions(line_spans, lines, width, cfg.captions.max_lines)

    bounds = {0.0, total}
    for item in [*screens, *marks, *captions]:
        bounds.update(min(max(t, 0.0), total) for t in (item.start, item.end))
    points = sorted(bounds)
    if len(points) == 1:
        points.append(total)

    for start, end in zip(points, points[1:]):
        if end <= start and len(points) > 2:
            continue
        state = _state_at(start, end, screens, marks, captions)
        previous = timeline.states[-1] if timeline.states else None
        if previous is not None and previous.same_content(state):
            previous.end = end
        else:
            timeline.states.append(state)
    return timeline


def caption_text(line: str) -> str:
    text = HEADING_PREFIX_RE.sub("", line, count=1)
    return " ".join(_strip_inline(text).split())


def _strip_inline(text: str) -> str:
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        spans.append(match.group(2))
        return f"\x00{len(spans) - 1}\x00"

    text = LINK_RE.sub(r"\1", CODE_SPAN_RE.sub(stash, text))
    previous = None
    while previous != text:
        previous = text
        for pattern in EMPHASIS_RES:
            text = pattern.sub(r"\2", text)
    return PLACEHOLDER_RE.sub(lambda match: spans[int(match.group(1))], text)


def _default_chars_per_line(cfg: VideoConfig) -> int:
    return max(1, int(cfg.size[0] * 0.9 / (cfg.captions.font_px * 0.55)))


def _code_lines(cues: list[Cue]) -> set[int]:
    inside: set[int] = set()
    for cue in cues:
        if isinstance(cue, CodeCue):
            inside.update(range(cue.line_no + 1, cue.line_no + len(cue.lines) + 2))
    return inside


def _events(
    cues: list[Cue],
    line_spans: dict[int, tuple[float, float]],
    lines: list[str],
    cfg: VideoConfig,
    total: float,
    warnings: list[CueWarning],
) -> list[_Event]:
    spoken = sorted(line_spans)

    def next_spoken(line_no: int) -> int | None:
        index = bisect_left(spoken, line_no)
        return spoken[index] if index < len(spoken) else None

    events: list[_Event] = []
    for order, cue in enumerate(cues):
        target = next_spoken(cue.line_no)
        if target is None:
            if not (isinstance(cue, CodeCue) and cue.hide):
                warnings.append(
                    CueWarning(cue.line_no, "no spoken line after tag; tag ignored")
                )
            continue
        start, end = line_spans[target]
        if target == cue.line_no:
            length = len(lines[target - 1]) if target <= len(lines) else 0
            ratio = min(cue.col / length, 1.0) if length else 0.0
            anchor = start + ratio * (end - start)
            key = (target, OWN_LINE_RANK, cue.col, order)
        else:
            anchor = start
            key = (target, ATTRIBUTED_RANK, 0, order)
        events.append(_Event(key, max(0.0, anchor - cfg.lead), anchor, cue))

    inside_code = _code_lines(cues)
    for line_no, line in enumerate(lines, start=1):
        if line_no in inside_code or not HEADING_RE.match(line):
            continue
        target = next_spoken(line_no)
        if target is None:
            continue
        start = line_spans[target][0]
        events.append(_Event((target, HEADING_RANK, 0, line_no), start, start, None))

    events.sort(key=lambda event: event.key)
    floor = 0.0
    for event in events:
        if event.cue is not None:
            event.time = max(event.time, floor)
            floor = event.time
    return events


def _screens(
    events: list[_Event], total: float, gap: float, warnings: list[CueWarning]
) -> list[_Screen]:
    screens: list[_Screen] = []
    current: _Screen | None = None
    floor = 0.0

    for event in events:
        cue = event.cue
        if cue is not None:
            event.time = max(event.time, floor)
        if cue is None or isinstance(cue, ClearCue):
            if current is not None:
                current.stop = event.time
            current = None
        elif isinstance(cue, ImageCue):
            joins = (
                current is not None
                and current.images
                and event.anchor - current.last_anchor < gap
            )
            if not joins:
                current = _open_screen(screens, current, event)
                floor = event.time
            current.images.append(cue)
            current.last_anchor = event.anchor
            if cue.duration is not None:
                current.duration = max(current.duration or 0.0, cue.duration)
        elif isinstance(cue, CodeCue) and not cue.hide:
            current = _open_screen(screens, current, event)
            current.code = cue
            floor = event.time
        event.screen = current
        event.seen = len(current.images) if current is not None else 0

    for screen, following in zip(screens, [*screens[1:], None]):
        end = min(screen.stop, total)
        if following is not None:
            end = min(end, following.start)
        if screen.duration is not None:
            end = min(end, screen.last_anchor + screen.duration)
        screen.end = max(end, screen.start)
        _warn_short(warnings, screen.line_no, "screen", screen.start, screen.end)
    return screens


def _open_screen(screens: list[_Screen], current: _Screen | None, event: _Event) -> _Screen:
    if screens:
        event.time = max(event.time, screens[-1].anchor)
    if current is not None:
        current.stop = event.time
    screen = _Screen(start=event.time, anchor=event.anchor, line_no=event.cue.line_no)
    screens.append(screen)
    return screen


def _warn_short(
    warnings: list[CueWarning], line_no: int, what: str, start: float, end: float
) -> None:
    if end - start < MIN_VISIBLE:
        warnings.append(
            CueWarning(line_no, f"{what} visible for only {end - start:.2f}s (< {MIN_VISIBLE}s)")
        )


def _marks(events: list[_Event], warnings: list[CueWarning]) -> list[_Mark]:
    marks: list[_Mark] = []
    last: dict[int, _Mark] = {}

    for event in events:
        cue = event.cue
        if not isinstance(cue, (PointerCue, LinePointerCue)):
            continue
        screen = event.screen
        alive = screen is not None and event.time < screen.end
        mark: _Mark | None = None

        if isinstance(cue, PointerCue):
            if not alive or screen.code is not None:
                warnings.append(
                    CueWarning(cue.line_no, "pointer without an image on screen; ignored")
                )
                continue
            if cue.ref is not None:
                target = next((img for img in screen.images if img.ref == cue.ref), None)
                if target is None:
                    warnings.append(
                        CueWarning(
                            cue.line_no, f"pointer target {cue.ref!r} is not on screen; ignored"
                        )
                    )
                    continue
            else:
                target = screen.images[event.seen - 1]
            pointer = PointerView(cue, target)
            mark = _Mark(event.time, screen.end, screen, cue.line_no, pointer=pointer)
        else:
            if not alive or screen.code is None:
                warnings.append(
                    CueWarning(cue.line_no, "line pointer without a code block on screen; ignored")
                )
                continue
            if cue.end > len(screen.code.lines):
                warnings.append(
                    CueWarning(
                        cue.line_no,
                        f"lines {cue.start}-{cue.end} outside the code block "
                        f"({len(screen.code.lines)} lines); ignored",
                    )
                )
                continue
            lines = (cue.start, cue.end)
            mark = _Mark(event.time, screen.end, screen, cue.line_no, lines=lines)
            if screen.first_lines is None:
                screen.first_lines = mark.lines

        if cue.duration is not None:
            mark.end = min(mark.end, event.anchor + cue.duration)
        previous = last.get(id(screen))
        if previous is not None:
            previous.end = min(previous.end, mark.start)
        last[id(screen)] = mark
        marks.append(mark)

    for mark in marks:
        _warn_short(warnings, mark.line_no, "pointer", mark.start, mark.end)
    return marks


def _captions(
    line_spans: dict[int, tuple[float, float]],
    lines: list[str],
    width: int,
    max_lines: int,
) -> list[_Caption]:
    captions: list[_Caption] = []
    for line_no in sorted(line_spans):
        if line_no < 1 or line_no > len(lines):
            continue
        text = caption_text(lines[line_no - 1])
        if not text:
            continue
        start, end = line_spans[line_no]
        wrapped = textwrap.wrap(text, width=width)
        if len(wrapped) <= max_lines:
            captions.append(_Caption(start, end, text))
            continue
        chunks = [" ".join(wrapped[i:i + max_lines]) for i in range(0, len(wrapped), max_lines)]
        weight = sum(len(chunk) for chunk in chunks)
        cursor = start
        done = 0
        for index, chunk in enumerate(chunks):
            done += len(chunk)
            chunk_end = end if index == len(chunks) - 1 else start + (end - start) * done / weight
            captions.append(_Caption(cursor, chunk_end, chunk))
            cursor = chunk_end
    return captions


def _state_at(
    start: float,
    end: float,
    screens: list[_Screen],
    marks: list[_Mark],
    captions: list[_Caption],
) -> ScreenState:
    state = ScreenState(start=start, end=end)
    screen = next((s for s in screens if s.start <= start < s.end), None)
    if screen is not None:
        mark = next(
            (m for m in marks if m.screen is screen and m.start <= start < m.end), None
        )
        if screen.code is not None:
            seen = [m for m in marks if m.screen is screen and m.start <= start]
            focus = seen[-1].lines if seen else screen.first_lines
            highlight = mark.lines if mark is not None else None
            state.code = CodeView(screen.code, focus=focus, highlight=highlight)
        else:
            state.images = list(screen.images)
            state.pointer = mark.pointer if mark is not None else None
    caption = next((c for c in captions if c.start <= start < c.end), None)
    state.caption = caption.text if caption is not None else None
    return state
