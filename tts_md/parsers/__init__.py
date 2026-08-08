from __future__ import annotations

from tts_md.parsers.base import Parser
from tts_md.parsers.codeblock import CodeBlockParser
from tts_md.parsers.emoji import EmojiParser
from tts_md.parsers.filepath import FilePathParser
from tts_md.parsers.function import FunctionParser
from tts_md.parsers.language import LanguageParser
from tts_md.parsers.markdown import MarkdownParser
from tts_md.parsers.url import UrlParser
from tts_md.parsers.uuid import UuidParser


def get_parsers() -> list[Parser]:
    parsers = [
        CodeBlockParser(),
        LanguageParser(),
        UuidParser(),
        FilePathParser(),
        FunctionParser(),
        UrlParser(),
        MarkdownParser(),
        EmojiParser(),
    ]
    return sorted(parsers, key=lambda p: p.priority)


PARSERS = get_parsers()

__all__ = ["PARSERS", "get_parsers"]
