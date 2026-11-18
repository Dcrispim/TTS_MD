"""Atribuicao de falas de light novels traduzidas por fas (plugin `novel-dialogue`).

No papel, quem fala fica claro pela tipografia; em voz alta o nome solto "flutua":

    「...como devemos responder?」 (Mensageiro)   -> ...? perguntou Mensageiro.
    (Ele esta crescendo rapido!) (Gazelle)        -> ...! pensou Gazelle.
    – Vamos embora – Rachel.                      -> – Vamos embora – disse Rachel.

O verbo sai de listas de sinonimos, escolhido por um hash da propria linha: o
mesmo paragrafo recebe sempre o mesmo verbo (deterministico) e o livro varia.
Linhas que ja tem verbo de fala ("– perguntou Urakin") nao sao tocadas.
"""

from __future__ import annotations

import re
import zlib
from collections import Counter

SPEECH_VERBS = ["disse", "falou", "comentou", "afirmou"]
QUESTION_VERBS = ["perguntou", "questionou", "indagou"]
EXCLAIM_VERBS = ["exclamou", "disse"]
THOUGHT_VERBS = ["pensou"]

# Verbos "dicendi" que indicam que a atribuicao ja existe: nesse caso nada muda.
KNOWN_VERBS = {
    *SPEECH_VERBS, *QUESTION_VERBS, *EXCLAIM_VERBS, *THOUGHT_VERBS,
    "respondeu", "gritou", "berrou", "murmurou", "sussurrou", "retrucou", "explicou",
    "concordou", "discordou", "pediu", "ordenou", "insistiu", "continuou", "completou",
    "acrescentou", "replicou", "rebateu", "repetiu", "suspirou", "riu", "brincou",
    "declarou", "anunciou", "avisou", "alertou", "implorou", "sugeriu", "ousou",
    "observou", "admitiu", "confessou", "protestou", "resmungou", "reclamou",
    "interrompeu", "chamou", "falou", "fala", "diz", "pergunta", "responde",
}

DASH = "–—"
# Nome de quem fala: palavras capitalizadas, com "de/da/dos..." no meio
# ("Gazelle", "Lorde Rimuru", "Chefe dos Homens-Lagarto"), ou "???" (desconhecido).
_CAP = r"[A-ZÀ-Ý][\w'’-]*"
# Figurantes numerados: "Ancião Elfo 1", "Multidão #2" (o "#" sai na leitura).
NAME = rf"{_CAP}(?:\s+(?:(?:de|da|do|dos|das|e)\s+)?{_CAP}){{0,3}}(?:\s+#?\d+)?"
UNKNOWN_RE = re.compile(r"\?{2,}")
TAG_RE = re.compile(rf"\s*\((?P<name>{NAME}|\?{{2,}})\)(?=\s|$)")
UNKNOWN_SPEAKER = "alguém"
CLOSERS = "」』\"”’»)"
OPENERS = "「『\"“‘«(" + DASH
# "– fala – Nome." no fim da linha (ou antes de nova fala "– ...").
DASH_NAME_RE = re.compile(
    rf"(?P<lead>\s[{DASH}]\s)(?P<name>[A-ZÀ-Ý][\w'’]*)(?P<end>[.!?…]?)(?=\s*$|\s+[{DASH}]\s)"
)
MID_SENTENCE_CAP_RE = re.compile(r"(?<=[a-zà-ÿ,;] )([A-ZÀ-Ý][\w'’]+)")


def _pick(options: list[str], key: str) -> str:
    return options[zlib.crc32(key.encode("utf-8")) % len(options)]


def _ending(text: str) -> str:
    """Pontuacao final da fala, ignorando colchetes/aspas de fechamento."""
    core = text.rstrip().rstrip(CLOSERS).rstrip()
    return core[-1:] if core else ""


def _verb_for(body: str, key: str, *, thought: bool = False) -> str:
    if thought:
        return _pick(THOUGHT_VERBS, key)
    end = _ending(body)
    if end == "?":
        return _pick(QUESTION_VERBS, key)
    if end == "!":
        return _pick(EXCLAIM_VERBS, key)
    return _pick(SPEECH_VERBS, key)


def _has_verb(text: str) -> bool:
    return any(word in KNOWN_VERBS for word in re.findall(r"\w+", text.lower()))


def _join(body: str, verb: str, name: str) -> str:
    """Fala + ", disse Nome." mantendo ?/!/… da fala e trocando o ponto final por virgula."""
    body = body.rstrip()
    closers = ""
    while body and body[-1] in CLOSERS and not (body[-1] == ")" and body.startswith("(")):
        closers = body[-1] + closers
        body = body[:-1].rstrip()
    thought_close = ""
    if body.startswith("(") and body.endswith(")"):
        body, thought_close = body[:-1].rstrip(), ")"
    if body.endswith(".."):  # reticencias escritas com pontos: ja sao pausa
        pass
    elif body.endswith("."):
        body = body[:-1] + ","
    elif body and body[-1] not in "?!…,":
        body += ","
    return f"{body}{thought_close}{closers} {verb} {name}."


def _attribute_tag(line: str) -> str | None:
    """Troca cada "(Nome)" que fecha uma fala/pensamento pelo verbo + nome.

    Vale no fim da linha e no meio ("「...」 (Rimuru) 「...」 (Ramiris)"). O trecho
    antes do nome tem que ser fala: terminar num fechamento (」 』 " ) ou, no fim
    da linha, comecar como fala. "Ele leu o capitulo (Gazelle)" e' narracao e fica.
    """
    out: list[str] = []
    prev = 0
    changed = False
    for n, match in enumerate(TAG_RE.finditer(line)):
        segment = line[prev : match.start()]
        name = match.group("name")
        stripped = segment.strip()
        rest = line[match.end():].strip()
        at_end = not rest
        # No meio da linha: logo depois de 」/』 (so fecham fala) ou antes de outra fala
        # ("(Rimuru) 「Mesmo?..."). Ja '" (I Don't Know Why) But I Do' e' titulo em texto corrido.
        if not at_end and not (stripped[-1:] in "」』" or rest[0] in OPENERS):
            continue
        closes = bool(stripped) and stripped[-1] in CLOSERS
        if closes and stripped[-1] == '"' and stripped.count('"') % 2:
            closes = False  # aspa reta impar: ela abre a citacao, nao fecha
        is_speech = bool(stripped) and (closes or (at_end and stripped[0] in OPENERS))
        if not is_speech or _has_verb(name):
            continue
        thought = stripped.endswith(")") and "(" in stripped
        speaker = UNKNOWN_SPEAKER if UNKNOWN_RE.fullmatch(name) else name.replace("#", "")
        verb = _verb_for(segment, f"{line}#{n}", thought=thought)
        lead = segment[: len(segment) - len(segment.lstrip())]
        out.append(lead + _join(stripped, verb, speaker))
        prev = match.end()
        changed = True
    if not changed:
        return None
    out.append(line[prev:])
    return "".join(out)


def proper_names(text: str) -> set[str]:
    """Palavras que aparecem com maiuscula no meio da frase pelo menos 2 vezes: nomes
    proprios do livro ("Rachel", "Levih"), e nao verbos/inicios de frase ("Nunca")."""
    counts = Counter(MID_SENTENCE_CAP_RE.findall(text))
    return {word for word, n in counts.items() if n >= 2}


def _attribute_dash(line: str, names: set[str]) -> str | None:
    if not line.lstrip().startswith(tuple(DASH)):
        return None
    # Trechos: fala (pares) e narracao (impares), separados por travessao com espacos.
    out, changed = line, False
    for match in reversed(list(DASH_NAME_RE.finditer(line))):
        name = match.group("name")
        if name not in names:
            continue
        before = line[: match.start()]
        segment_index = len(re.findall(rf"\s[{DASH}]\s", " " + before.lstrip().lstrip(DASH)))
        if segment_index % 2 != 0:  # o travessao abre narracao so depois de um trecho de fala
            continue
        verb = _verb_for(before, line)
        end = match.group("end") or "."
        out = out[: match.start()] + f"{match.group('lead')}{verb} {name}{end}" + out[match.end():]
        changed = True
    return out if changed else None
