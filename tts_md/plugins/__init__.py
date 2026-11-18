"""Plugins de blocos: ajustes de texto para um tipo de livro, obra ou autor.

A base (conversao, conserto de palavras partidas, parsers) e' generica. O que so faz
sentido para um tipo de livro - p.ex. o "(Gazelle)" no fim das falas das light
novels traduzidas por fas - mora num plugin isolado em `tts_md/plugins/<pacote>/`
e nao contamina o resto.

Nada roda sozinho: os plugins sao escolhidos explicitamente na leitura,
`tts-md --book X --plugins novel-dialogue[,outro]`, e aplicados nessa ordem.
`tts-md --list-plugins` lista os disponiveis (o biblio usa isso para a selecao).

Contrato: cada bloco (uma linha do Markdown = um paragrafo) entra em `handle()` e
sai com o texto final. Uma linha continua uma linha, entao secoes e progresso
(--track) nao se deslocam. `prepare()` ve o livro inteiro antes, para plugins que
precisam de contexto global (ex.: quais palavras sao nomes proprios).
"""

from __future__ import annotations


class BlockPlugin:
    """Base dos plugins. Subclasses definem `slug`, `description` e `handle()`."""

    slug: str = ""
    description: str = ""

    def prepare(self, markdown: str) -> None:
        """Contexto do livro inteiro, antes dos blocos. Padrao: nada."""

    def handle(self, block: str) -> str:
        raise NotImplementedError


def registry() -> dict[str, type[BlockPlugin]]:
    # Import tardio: os plugins so carregam quando a CLI de fato le um livro.
    from tts_md.plugins.novel_dialogue import NovelDialoguePlugin

    return {cls.slug: cls for cls in (NovelDialoguePlugin,)}


def parse_slugs(values: tuple[str, ...] | list[str]) -> list[str]:
    """`--plugins a,b --plugins c` -> ["a", "b", "c"], sem repetir, na ordem."""
    slugs: list[str] = []
    for value in values:
        for slug in value.split(","):
            slug = slug.strip()
            if slug and slug not in slugs:
                slugs.append(slug)
    return slugs


def load(slugs: list[str]) -> list[BlockPlugin]:
    available = registry()
    unknown = [slug for slug in slugs if slug not in available]
    if unknown:
        raise ValueError(
            f"unknown plugin(s): {', '.join(unknown)}; available: {', '.join(sorted(available))}"
        )
    return [available[slug]() for slug in slugs]


def apply(plugins: list[BlockPlugin], markdown: str) -> tuple[str, dict[str, int]]:
    """Passa cada bloco pelos plugins, em ordem. Devolve (texto, blocos alterados por plugin)."""
    changed: dict[str, int] = {}
    for plugin in plugins:
        plugin.prepare(markdown)
        lines = markdown.split("\n")
        count = 0
        for i, line in enumerate(lines):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            new = plugin.handle(line)
            if "\n" in new:
                raise ValueError(f"plugin '{plugin.slug}' turned one block into several lines")
            if new != line:
                lines[i] = new
                count += 1
        markdown = "\n".join(lines)
        changed[plugin.slug] = count
    return markdown, changed
