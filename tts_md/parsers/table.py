from __future__ import annotations

import re

from tts_md.models import SpeechBlock
from tts_md.parsers.base import Parser

SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")


class TableParser(Parser):
    """Detecta uma tabela Markdown e a resume numa unica fala.

    Ler pipes e celulas em voz alta e' incompreensivel; quem ouve so precisa
    saber que ha uma tabela e que o conteudo esta no arquivo.

    Nao entra em PARSERS: detectar o inicio exige olhar a linha seguinte (o
    cabecalho so e' tabela se a proxima linha for o separador), o que o loop
    generico de um parser por linha nao suporta. O engine chama os metodos
    abaixo diretamente, como ja faz com CodeBlockParser.toggles_block.
    """

    priority = 12

    def is_row(self, line: str) -> bool:
        stripped = line.strip()
        return bool(stripped) and "|" in stripped

    def is_separator(self, line: str) -> bool:
        cells = self._cells(line)
        return bool(cells) and all(SEPARATOR_CELL_RE.match(c.strip()) for c in cells)

    def starts_table(self, line: str, next_line: str | None) -> bool:
        if next_line is None:
            return False
        return self.is_row(line) and not self.is_separator(line) and self.is_separator(next_line)

    def table_length(self, lines: list[str], start: int) -> int:
        """Numero de linhas consumidas a partir de `start` (cabecalho + separador + corpo)."""
        index = start + 2
        while index < len(lines) and self.is_row(lines[index]):
            index += 1
        return index - start

    def announcement(self, *, default_lang: str) -> SpeechBlock:
        return SpeechBlock(text="Leia a tabela no arquivo.", lang=default_lang)

    @staticmethod
    def _cells(line: str) -> list[str]:
        trimmed = line.strip()
        if trimmed.startswith("|"):
            trimmed = trimmed[1:]
        if trimmed.endswith("|"):
            trimmed = trimmed[:-1]
        return trimmed.split("|")

    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        if in_code_block:
            return False
        return self.is_row(line)

    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        return [SpeechBlock(text="", speak=False, lang=default_lang)]
