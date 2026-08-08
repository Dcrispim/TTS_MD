from __future__ import annotations

import re

from tts_md.models import SpeechBlock
from tts_md.parsers.base import Parser

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)


class EmojiParser(Parser):
    priority = 60

    def match(self, line: str, *, in_code_block: bool = False) -> bool:
        if in_code_block:
            return False
        return bool(_EMOJI_RE.search(line))

    def parse(
        self,
        line: str,
        *,
        default_lang: str = "pt-BR",
        in_code_block: bool = False,
    ) -> list[SpeechBlock]:
        cleaned = _EMOJI_RE.sub("", line)
        cleaned = " ".join(cleaned.split())
        if not cleaned:
            return []
        return [SpeechBlock(text=cleaned, lang=default_lang)]
