from __future__ import annotations

import re

from tts_md.lang_index import Term, load_index
from tts_md.models import SpeechBlock
from tts_md.parsers.base import Parser


class LangIndexParser(Parser):
    """Troca o idioma palavra a palavra conforme o lang_index.yaml.

    Nao entra em PARSERS: o engine so aplica um parser por linha, e este precisa
    valer para todas elas. Roda como refinamento do que os outros produziram.
    """

    priority = 70
    # So letras: "config.yaml" vira as palavras "config" e "yaml".
    WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

    def __init__(self, index: dict[str, Term] | None = None):
        self.index = load_index() if index is None else index

    def lookup(self, word: str) -> Term | None:
        return self.index.get(word.lower())

    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        if in_code_block:
            return False
        return any(
            self.lookup(m.group(0)) is not None for m in self.WORD_RE.finditer(line)
        )

    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        return [
            SpeechBlock(text=text, lang=lang)
            for lang, text in self.split(line, default_lang=default_lang)
            if text.strip()
        ]

    def refine(
        self,
        blocks: list[SpeechBlock],
        *,
        default_lang: str,
    ) -> list[SpeechBlock]:
        """Subdivide os blocos que sobraram no idioma padrao.

        Quem ja tem idioma definido (tag {lang:...}, nome de funcao) fica como
        esta: o indice e' o fallback, nao a ultima palavra.
        """
        refined: list[SpeechBlock] = []

        for block in blocks:
            if block.lang != default_lang:
                refined.append(block)
                continue

            pieces = self.split(block.text, default_lang=default_lang)
            if len(pieces) == 1 and pieces[0][1] == block.text:
                refined.append(block)
                continue

            for lang, text in pieces:
                if not text.strip():
                    continue
                refined.append(
                    SpeechBlock(
                        text=text,
                        lang=lang,
                        speak=block.speak,
                        voice=block.voice,
                        speed=block.speed,
                        pause_after=block.pause_after,
                        line_no=block.line_no,
                    )
                )

        return refined

    def split(self, text: str, *, default_lang: str) -> list[tuple[str, str]]:
        """Quebra o texto em (idioma, trecho), juntando palavras vizinhas do mesmo."""
        pieces: list[tuple[str, str]] = []
        current_lang: str | None = None
        buffer = ""
        cursor = 0

        for match in self.WORD_RE.finditer(text):
            term = self.lookup(match.group(0))
            lang = term.lang if term else default_lang
            spoken = term.text if term and term.text else match.group(0)
            gap = text[cursor : match.start()]

            if current_lang is None:
                buffer = gap + spoken
                current_lang = lang
            elif lang == current_lang:
                buffer += gap + spoken
            else:
                # A pontuacao entre as palavras fica com o trecho que ja vinha.
                pieces.append((current_lang, buffer + gap))
                buffer = spoken
                current_lang = lang

            cursor = match.end()

        tail = text[cursor:]
        if current_lang is None:
            return [(default_lang, text)]

        pieces.append((current_lang, buffer + tail))
        return pieces
