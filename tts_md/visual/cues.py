from __future__ import annotations

import re
from dataclasses import dataclass, field

from tts_md.parsers.codeblock import CodeBlockParser

FENCE_RE = re.compile(r"^(\s*`{3,})(.*)$")
TAG_RE = re.compile(
    r"(?P<code>(?<!`)(?P<ticks>`+)(?!`)[^\n]*?(?<!`)(?P=ticks)(?!`))"
    r"|!\[\[(?P<image>[^\]\n]*)\]\]"
    r"|!\{(?P<brace>[^}\n]*)\}"
    r"|(?P<open>!\[\[|!\{)"
)
BLOCK_PREFIX_RE = re.compile(r"\s*(?:(?:[-*+>]|\d+[.)]|#{1,6})\s+)*")
TAG_START_RE = re.compile(r"!(\[\[|\{)")
WORD_CHAR_RE = re.compile(r"\w")
PUNCT_CHARS = frozenset(",.;:!?)")
DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s)$")
LINE_RE = re.compile(r"^L(\d+)(?:-(\d+))?$")
POINT_RE = re.compile(
    r"^(?:(?P<ref>[^@]+)@)?"
    r"(?P<x>\d+(?:\.\d+)?)(?P<x_pct>%)?\s*,\s*"
    r"(?P<y>\d+(?:\.\d+)?)(?P<y_pct>%)?$"
)
HIDE_RE = re.compile(r"!hide(?=\s|$)")


@dataclass
class Cue:
    line_no: int
    col: int


@dataclass
class ImageCue(Cue):
    ref: str
    duration: float | None = None


@dataclass
class PointerCue(Cue):
    x: float
    y: float
    x_percent: bool = False
    y_percent: bool = False
    ref: str | None = None
    duration: float | None = None


@dataclass
class LinePointerCue(Cue):
    start: int
    end: int
    duration: float | None = None


@dataclass
class ClearCue(Cue):
    pass


@dataclass
class CodeCue(Cue):
    lang: str
    lines: list[str] = field(default_factory=list)
    hide: bool = False


@dataclass
class CueWarning:
    line_no: int
    message: str


@dataclass
class CueExtraction:
    markdown: str
    cues: list[Cue] = field(default_factory=list)
    warnings: list[CueWarning] = field(default_factory=list)


class InvalidTag(ValueError):
    pass


def extract_cues(markdown: str) -> CueExtraction:
    result = CueExtraction(markdown="")
    out: list[str] = []
    code: CodeCue | None = None

    for line_no, raw in enumerate(markdown.splitlines(keepends=True), start=1):
        body = raw.splitlines()[0]
        ending = raw[len(body):]
        is_fence = bool(CodeBlockParser.FENCE_RE.match(body))

        if code is not None:
            if not is_fence:
                code.lines.append(body)
                out.append(raw)
                continue
            code = None
            clean, cues, warnings = _strip_tags(body, line_no)
            if cues or warnings:
                result.warnings.append(
                    CueWarning(line_no, "tag on closing fence ignored")
                )
            out.append(clean + ending)
            continue

        clean, cues, warnings = _strip_tags(body, line_no)
        if not is_fence and CodeBlockParser.FENCE_RE.match(clean):
            clean, cues = body, []
            warnings = [CueWarning(line_no, "removing tags would open a code fence; tags kept")]
        result.cues.extend(cues)
        result.warnings.extend(warnings)

        if is_fence:
            code, clean = _open_code(clean, line_no)
            result.cues.append(code)
        if not clean and not ending:
            clean = " "
        out.append(clean + ending)

    result.markdown = "".join(out)
    return result


def _open_code(line: str, line_no: int) -> tuple[CodeCue, str]:
    fence = FENCE_RE.match(line)
    info = fence.group(2)
    hide = bool(HIDE_RE.search(info))
    tokens = HIDE_RE.sub(" ", info).split()
    if hide:
        line = fence.group(1) + " ".join(tokens)
    cue = CodeCue(line_no=line_no, col=0, lang=tokens[0] if tokens else "", hide=hide)
    return cue, line


def _strip_tags(line: str, line_no: int) -> tuple[str, list[Cue], list[CueWarning]]:
    clean = ""
    pos = 0
    cues: list[Cue] = []
    warnings: list[CueWarning] = []
    protected = BLOCK_PREFIX_RE.match(line).end()
    for match in TAG_RE.finditer(line):
        if match.group("code") is not None:
            continue
        if match.group("open") is not None:
            warnings.append(CueWarning(line_no, f"{match.group(0)}: unclosed tag"))
            continue

        clean += line[pos:match.start()]
        pos = match.end()
        rest = line[pos:]
        if (not clean or clean[-1].isspace()) and rest[:1].isspace():
            pos += 1
        elif clean[-1:].isspace() and _starts_with_punct(rest):
            clean = clean[:max(len(clean.rstrip()), protected)]
            for cue in cues:
                cue.col = min(cue.col, len(clean))
        elif _is_word(clean[-1:]) and _is_word(rest[:1]):
            clean += " "

        try:
            cues.append(_parse_tag(match, line_no, len(clean)))
        except InvalidTag as exc:
            warnings.append(CueWarning(line_no, f"{match.group(0)}: {exc}"))

    return clean + line[pos:], cues, warnings


def _starts_with_punct(text: str) -> bool:
    return text[:1] in PUNCT_CHARS and not TAG_START_RE.match(text)


def _is_word(char: str) -> bool:
    return bool(WORD_CHAR_RE.match(char))


def _parse_tag(match: re.Match[str], line_no: int, col: int) -> Cue:
    if match.group("image") is not None:
        ref, duration = _split_duration(match.group("image"))
        if not ref:
            raise InvalidTag("empty image reference")
        return ImageCue(line_no=line_no, col=col, ref=ref, duration=duration)

    body, duration = _split_duration(match.group("brace"))

    if body == "clear":
        if duration is not None:
            raise InvalidTag("clear does not take a duration")
        return ClearCue(line_no=line_no, col=col)

    lines = LINE_RE.match(body)
    if lines:
        start = int(lines.group(1))
        end = int(lines.group(2) or start)
        if start < 1 or end < start:
            raise InvalidTag("invalid line range")
        return LinePointerCue(
            line_no=line_no, col=col, start=start, end=end, duration=duration
        )

    point = POINT_RE.match(body)
    if point:
        ref = point.group("ref")
        return PointerCue(
            line_no=line_no,
            col=col,
            x=float(point.group("x")),
            y=float(point.group("y")),
            x_percent=bool(point.group("x_pct")),
            y_percent=bool(point.group("y_pct")),
            ref=ref.strip() if ref and ref.strip() else None,
            duration=duration,
        )

    raise InvalidTag("unknown tag")


def _split_duration(body: str) -> tuple[str, float | None]:
    head, sep, tail = body.partition("|")
    if not sep:
        return head.strip(), None

    match = DURATION_RE.match(tail.strip())
    if not match:
        raise InvalidTag(f"invalid duration {tail.strip()!r} (use s or ms)")
    value = float(match.group(1))
    if match.group(2) == "ms":
        value /= 1000
    if value <= 0:
        raise InvalidTag("duration must be positive")
    return head.strip(), value
