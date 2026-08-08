from __future__ import annotations

import re

from tts_md.models import SpeechBlock
from tts_md.parsers.base import Parser


class LanguageParser(Parser):
    priority = 20

    LANG_TAG_RE = re.compile(
        r"\{(?:lang:([a-zA-Z]{2}(?:-[a-zA-Z]{2})?)|([a-zA-Z]{2}(?:-[a-zA-Z]{2})?))\}"
        r"(.*?)"
        r"\{(?:/lang|/\1|/\2)\}",
        re.DOTALL,
    )

    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        if in_code_block:
            return False
        return bool(self.LANG_TAG_RE.search(line))

    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        blocks: list[SpeechBlock] = []
        pos = 0

        for match in self.LANG_TAG_RE.finditer(line):
            before = line[pos : match.start()].strip()
            if before:
                blocks.append(SpeechBlock(text=before, lang=default_lang))

            lang = match.group(1) or match.group(2)
            text = match.group(3).strip()
            if text:
                blocks.append(SpeechBlock(text=text, lang=lang))
            pos = match.end()

        tail = line[pos:].strip()
        if tail:
            blocks.append(SpeechBlock(text=tail, lang=default_lang))

        return blocks
