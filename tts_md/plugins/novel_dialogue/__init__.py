"""Plugin `novel-dialogue`: atribui as falas de light novels traduzidas por fas.

Nessas traducoes (ex.: Tensei Shitara Slime) quem fala vem entre parenteses no fim da
fala - 「...」 (Gazelle) - e em voz alta o nome fica "solto". O plugin troca por um
verbo de fala: "...」 disse Gazelle.", "pensou" para pensamentos entre (), "perguntou"
para ?, "alguem" para (???). Tambem cobre "- fala - Nome." sem verbo.
Regras e validacoes em dialogue.py.
"""

from __future__ import annotations

from tts_md.plugins import BlockPlugin
from tts_md.plugins.novel_dialogue.dialogue import _attribute_dash, _attribute_tag, proper_names


class NovelDialoguePlugin(BlockPlugin):
    slug = "novel-dialogue"
    description = "falas de light novels: '「...」 (Gazelle)' vira '...」 disse Gazelle'"

    def __init__(self) -> None:
        self.names: set[str] = set()

    def prepare(self, markdown: str) -> None:
        self.names = proper_names(markdown)

    def handle(self, block: str) -> str:
        return _attribute_tag(block) or _attribute_dash(block, self.names) or block
