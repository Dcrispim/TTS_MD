from __future__ import annotations

from abc import ABC, abstractmethod

from tts_md.models import SpeechBlock


class Parser(ABC):
    priority: int = 100

    @abstractmethod
    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        ...

    @abstractmethod
    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        ...
