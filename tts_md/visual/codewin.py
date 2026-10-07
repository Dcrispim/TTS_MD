from __future__ import annotations

from dataclasses import dataclass

CONTEXT_ABOVE = 5


@dataclass(frozen=True)
class CodeWindow:
    first: int
    last: int
    truncated_focus: bool = False


def code_window(
    n_lines: int, max_lines: int, focus: tuple[int, int] | None
) -> CodeWindow:
    n = max(0, n_lines)
    x = max(1, max_lines)
    if n <= x:
        return CodeWindow(1, n)
    if focus is None:
        return CodeWindow(1, x)
    s = min(max(1, focus[0]), n)
    e = min(max(s, focus[1]), n)
    first = min(s, max(s - CONTEXT_ABOVE, e - x + 1))
    first = max(1, min(first, n - x + 1))
    last = first + x - 1
    return CodeWindow(first, last, truncated_focus=e > last)
