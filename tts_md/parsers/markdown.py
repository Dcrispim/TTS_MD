from __future__ import annotations

import os
import re

from tts_md.models import SpeechBlock
from tts_md.parsers.base import Parser


class MarkdownParser(Parser):
    priority = 50

    HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*)$")
    INLINE_CODE_RE = re.compile(r"`([^`]+)`")
    BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")

    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        if in_code_block:
            return False
        stripped = line.strip()
        if not stripped:
            return False
        return bool(
            self.HEADING_RE.match(stripped)
            or self.INLINE_CODE_RE.search(stripped)
            or self.BOLD_RE.search(stripped)
        )

    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        stripped = line.strip()
        heading = self.HEADING_RE.match(stripped)
        if heading:
            text = heading.group(2).strip()
            text = self._clean_inline(text)
            if text:
                return [SpeechBlock(text=text, lang=default_lang)]
            return []

        return self._parse_inline(stripped, default_lang=default_lang)

    def _parse_inline(self, text: str, *, default_lang: str) -> list[SpeechBlock]:
        blocks: list[SpeechBlock] = []
        pos = 0
        pattern = re.compile(
            r"`([^`]+)`|\*\*([^*]+)\*\*"
        )

        for match in pattern.finditer(text):
            before = text[pos : match.start()].strip()
            if before:
                blocks.append(SpeechBlock(text=before, lang=default_lang))

            if match.group(1):
                inline = match.group(1)
                if inline.startswith("/") and "." in inline:
                    filename = os.path.basename(inline)
                    blocks.append(
                        SpeechBlock(
                            text=f"arquivo {filename}",
                            lang=default_lang,
                        )
                    )
                else:
                    blocks.append(
                        SpeechBlock(text=inline, lang=default_lang)
                    )
            elif match.group(2):
                blocks.append(
                    SpeechBlock(text=match.group(2), lang=default_lang)
                )

            pos = match.end()

        tail = text[pos:].strip()
        if tail:
            blocks.append(SpeechBlock(text=tail, lang=default_lang))

        return blocks

    @classmethod
    def _clean_inline(cls, text: str) -> str:
        text = cls.BOLD_RE.sub(r"\1", text)
        text = cls.INLINE_CODE_RE.sub(r"\1", text)
        return text.strip()
