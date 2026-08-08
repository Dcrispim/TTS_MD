from __future__ import annotations

import re

from tts_md.models import SpeechBlock
from tts_md.parsers.base import Parser


class CodeBlockParser(Parser):
    priority = 10
    FENCE_RE = re.compile(r"^\s*```")

    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        return in_code_block or bool(self.FENCE_RE.match(line))

    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        return [SpeechBlock(text="", speak=False, lang=default_lang)]

    def toggles_block(self, line: str, *, in_code_block: bool) -> bool:
        return bool(self.FENCE_RE.match(line))
