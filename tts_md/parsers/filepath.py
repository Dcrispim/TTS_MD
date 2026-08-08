from __future__ import annotations

import os
import re

from tts_md.models import SpeechBlock
from tts_md.parsers.base import Parser


class FilePathParser(Parser):
    priority = 30
    PATH_RE = re.compile(r"(/[a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)")

    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        if in_code_block:
            return False
        return bool(self.PATH_RE.search(line))

    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        blocks: list[SpeechBlock] = []
        pos = 0

        for match in self.PATH_RE.finditer(line):
            before = line[pos : match.start()].strip()
            if before:
                blocks.append(SpeechBlock(text=before, lang=default_lang))

            path = match.group(1)
            filename = os.path.basename(path)
            blocks.append(
                SpeechBlock(text=f"arquivo {filename}", lang=default_lang)
            )
            pos = match.end()

        tail = line[pos:].strip()
        if tail:
            blocks.append(SpeechBlock(text=tail, lang=default_lang))

        return blocks
