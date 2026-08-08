from __future__ import annotations

import re

from tts_md.models import SpeechBlock
from tts_md.parsers.base import Parser


class FunctionParser(Parser):
    """Le chamadas de funcao (`approve()`, `Payment.approve()`) com voz em ingles."""

    priority = 35
    code_lang = "en-US"

    _IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
    # Parenteses vazios sempre casam. Com argumentos, exige um marcador de
    # codigo (`,` `=` `_` `.` aspas ou digito) para nao confundir com o plural
    # do portugues: "arquivo(s)", "item(ns)".
    _ARGS = r"\(\s*\)|\([^()]*[,=_.\"'0-9][^()]*\)"
    FUNC_RE = re.compile(
        rf"\b((?:{_IDENT}\s*\.\s*)*{_IDENT})\s*(?:{_ARGS})"
    )

    HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
    BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
    # Crases e asteriscos ficam orfaos quando a funcao sai do meio de `code`
    # ou **negrito**, e o MarkdownParser nao roda depois deste.
    MARKUP_RE = re.compile(r"`|\*\*")

    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        if in_code_block:
            return False
        return bool(self.FUNC_RE.search(line))

    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        blocks: list[SpeechBlock] = []
        pos = 0

        for match in self.FUNC_RE.finditer(line):
            before = self._clean(line[pos : match.start()], at_start=pos == 0)
            if before:
                blocks.append(SpeechBlock(text=before, lang=default_lang))

            spoken = self._to_speech(match.group(1))
            if spoken:
                blocks.append(SpeechBlock(text=spoken, lang=self.code_lang))
            pos = match.end()

        tail = self._clean(line[pos:], at_start=pos == 0)
        if tail:
            blocks.append(SpeechBlock(text=tail, lang=default_lang))

        return blocks

    @classmethod
    def _to_speech(cls, name: str) -> str:
        parts = [cls._humanize(part) for part in re.split(r"\s*\.\s*", name)]
        return " dot ".join(part for part in parts if part)

    @staticmethod
    def _humanize(ident: str) -> str:
        # snake_case e camelCase viram palavras separadas: o TTS ingles
        # pronuncia "get user by id", nao "getuserbyid".
        words = ident.replace("_", " ")
        words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", words)
        words = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", words)
        return " ".join(words.split())

    @classmethod
    def _clean(cls, text: str, *, at_start: bool) -> str:
        if at_start:
            text = cls.HEADING_RE.sub("", text)
            text = cls.BULLET_RE.sub("", text)
        text = cls.MARKUP_RE.sub("", text)
        return " ".join(text.split())
