from __future__ import annotations

import re

from tts_md.models import SpeechBlock
from tts_md.parsers.base import Parser


class UuidParser(Parser):
    """Le UUIDs como 'uuid de final XXXX' em vez do valor completo.

    Ler um UUID por extenso (32 caracteres em 5 grupos) e' incompreensivel de
    ouvido; os ultimos caracteres bastam para reconhecer de qual registro se
    trata numa lista.
    """

    priority = 25
    TAIL_LENGTH = 4

    UUID_RE = re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
        r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )

    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        if in_code_block:
            return False
        return bool(self.UUID_RE.search(line))

    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        blocks: list[SpeechBlock] = []
        pos = 0

        for match in self.UUID_RE.finditer(line):
            before = line[pos : match.start()].strip()
            if before:
                blocks.append(SpeechBlock(text=before, lang=default_lang))

            uuid = match.group(0)
            tail = uuid[-self.TAIL_LENGTH :]
            # Espacado para o TTS ler caractere a caractere, e nao tentar
            # pronunciar o trecho como numero ou palavra.
            spoken_tail = " ".join(tail)
            blocks.append(
                SpeechBlock(text=f"uuid de final {spoken_tail}", lang=default_lang)
            )
            pos = match.end()

        tail_text = line[pos:].strip()
        if tail_text:
            blocks.append(SpeechBlock(text=tail_text, lang=default_lang))

        return blocks
