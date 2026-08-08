from __future__ import annotations

import re

from tts_md.models import SpeechBlock
from tts_md.parsers.base import Parser


class UrlParser(Parser):
    priority = 40
    URL_RE = re.compile(r"https?://[^\s<>\"']+")

    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        if in_code_block:
            return False
        return bool(self.URL_RE.search(line))

    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        blocks: list[SpeechBlock] = []
        pos = 0

        for match in self.URL_RE.finditer(line):
            before = line[pos : match.start()].strip()
            if before:
                blocks.append(SpeechBlock(text=before, lang=default_lang))

            url = match.group(0)
            spoken = self._url_to_speech(url)
            blocks.append(SpeechBlock(text=spoken, lang=default_lang))
            pos = match.end()

        tail = line[pos:].strip()
        if tail:
            blocks.append(SpeechBlock(text=tail, lang=default_lang))

        return blocks

    @staticmethod
    def _url_to_speech(url: str) -> str:
        url = re.sub(r"^https?://", "", url)
        url = url.rstrip("/")
        url = url.replace(".", " ponto ")
        url = url.replace("/", " barra ")
        url = url.replace("-", " traço ")
        url = url.replace("_", " underline ")
        return " ".join(url.split())
