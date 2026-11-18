"""Conversao de livros (EPUB e, via Calibre, AZW3/MOBI/KFX...) para Markdown
pronto para leitura em voz alta.

So biblioteca padrao: o EPUB e' um zip com um OPF (manifest + spine) que
aponta para XHTMLs na ordem de leitura. Cada paragrafo vira uma linha do
Markdown (separada por linha em branco) e cada titulo vira `#`, que e' a
unidade que o TTSEngine fala de uma vez.

Fica de fora o que atrapalha a escuta: imagens, chamadas de nota de rodape
(<sup>, epub:type="noteref"), as proprias notas, quebras de pagina e o
sumario de navegacao do EPUB3.
"""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

CONTAINER_PATH = "META-INF/container.xml"
XHTML_TYPES = {"application/xhtml+xml", "text/html"}

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
BLOCK_TAGS = {
    "p", "div", "section", "article", "blockquote", "li", "ul", "ol",
    "dd", "dt", "tr", "table", "pre", "figure", "figcaption", "header",
    "footer", "hr", *HEADINGS,
}
# Conteudo inteiro ignorado (com os filhos).
SKIP_TAGS = {"head", "script", "style", "svg", "math", "sup", "nav", "rt", "rp"}
SKIP_EPUB_TYPES = {
    "noteref", "footnote", "endnote", "rearnote", "footnotes", "endnotes",
    "rearnotes", "pagebreak", "toc", "landmarks", "page-list",
}
VOID_TAGS = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr"}

SPACE_RE = re.compile(r"\s+")
# Uma linha comecando com isso mudaria o significado no parser de Markdown
# (bloco de codigo, tag de video, tabela): esses marcadores saem do texto.
UNSAFE_START_RE = re.compile(r"^(```|~~~|!\{|!\[\[|\|)")


class EpubError(Exception):
    pass


@dataclass
class EpubBook:
    title: str
    markdown: str
    chapters: int = 0
    paragraphs: int = 0
    # Linhas (1-based) do Markdown onde comeca cada documento do spine (capitulo,
    # sumario, prefacio...), para a tecla de pular secao.
    section_lines: list[int] = field(default_factory=list)
    # Palavras partidas ("tom ei", "j ogada") juntadas por textfix.
    repaired: int = 0
    warnings: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class _XhtmlToMarkdown(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buffer: list[str] = []
        self._heading: int | None = None
        self._skip_depth = 0
        self._stack: list[tuple[str, bool]] = []  # (tag aberta, ela iniciou um skip?)
        self._list_item = False

    # -- helpers ---------------------------------------------------------
    def _flush(self) -> None:
        text = SPACE_RE.sub(" ", "".join(self._buffer)).strip()
        self._buffer.clear()
        if not text:
            self._list_item = False
            return
        text = UNSAFE_START_RE.sub("", text).strip()
        if not text:
            self._list_item = False
            return
        if self._heading:
            self.lines.append(f"{'#' * self._heading} {text}")
        elif self._list_item:
            self.lines.append(f"- {text}")
        else:
            self.lines.append(text)
        self._list_item = False

    @staticmethod
    def _skips(tag: str, attrs: dict[str, str | None]) -> bool:
        if tag in SKIP_TAGS:
            return True
        epub_type = attrs.get("epub:type") or attrs.get("role") or ""
        kinds = {k.removeprefix("doc-") for k in epub_type.split()}
        if kinds & SKIP_EPUB_TYPES:
            return True
        return tag == "aside" and bool(kinds)

    # -- HTMLParser ------------------------------------------------------
    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs = {k.lower(): v for k, v in attrs_list}
        if tag in VOID_TAGS:
            if tag == "br" and not self._skip_depth:
                self._buffer.append(" ")
            return
        starts_skip = self._skips(tag, attrs)
        self._stack.append((tag, starts_skip))
        if starts_skip:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in HEADINGS:
                self._heading = HEADINGS[tag]
            elif tag == "li":
                self._list_item = True

    def handle_startendtag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        # <div/> e afins em XHTML: nao abrem escopo nenhum.
        if tag.lower() == "br" and not self._skip_depth:
            self._buffer.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS or all(open_tag != tag for open_tag, _ in self._stack):
            return
        # HTML frouxo (<p> sem </p>): fecha tudo que ficou aberto ate a tag certa.
        while True:
            open_tag, started_skip = self._stack.pop()
            if started_skip:
                self._skip_depth -= 1
            if open_tag == tag:
                break
        if started_skip:
            return
        if self._skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in HEADINGS:
                self._heading = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._buffer.append(data.replace("\xad", "").replace("\xa0", " "))

    def close(self) -> None:
        super().close()
        self._flush()


def xhtml_to_lines(html: str) -> list[str]:
    parser = _XhtmlToMarkdown()
    parser.feed(html)
    parser.close()
    return parser.lines


def _rootfile(zf: zipfile.ZipFile) -> str:
    try:
        root = ET.fromstring(zf.read(CONTAINER_PATH))
    except KeyError as exc:
        raise EpubError(f"{CONTAINER_PATH} not found: not a valid EPUB") from exc
    for el in root.iter():
        if _local(el.tag) == "rootfile" and el.get("full-path"):
            return el.get("full-path")
    raise EpubError("container.xml has no rootfile")


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_epub(path: Path) -> EpubBook:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise EpubError(f"Could not open {path} as an EPUB: {exc}") from exc

    with zf:
        opf_path = _rootfile(zf)
        opf_dir = posixpath.dirname(opf_path)
        try:
            opf = ET.fromstring(zf.read(opf_path))
        except (KeyError, ET.ParseError) as exc:
            raise EpubError(f"Could not read the package file {opf_path}: {exc}") from exc

        title = ""
        manifest: dict[str, tuple[str, str, str]] = {}
        spine: list[str] = []
        for el in opf.iter():
            name = _local(el.tag)
            if name == "title" and not title and el.text:
                title = el.text.strip()
            elif name == "item":
                manifest[el.get("id", "")] = (
                    el.get("href", ""),
                    el.get("media-type", ""),
                    el.get("properties", ""),
                )
            elif name == "itemref" and el.get("linear", "yes") != "no":
                spine.append(el.get("idref", ""))

        book = EpubBook(title=title or path.stem, markdown="")
        lines: list[str] = [f"# {book.title}"] if title else []
        for idref in spine:
            item = manifest.get(idref)
            if item is None:
                book.warnings.append(f"spine item '{idref}' missing from the manifest")
                continue
            href, media_type, properties = item
            if media_type not in XHTML_TYPES or "nav" in properties.split():
                continue
            member = posixpath.normpath(posixpath.join(opf_dir, unquote(href)))
            try:
                html = _decode(zf.read(member))
            except KeyError:
                book.warnings.append(f"{member} listed in the spine but missing from the zip")
                continue
            chapter = xhtml_to_lines(html)
            if not chapter:
                continue
            book.chapters += 1
            # Titulo do livro repetido na capa/folha de rosto nao precisa ser lido duas vezes.
            if lines and chapter and chapter[0].lstrip("# ") == book.title:
                chapter = chapter[1:]
            if chapter:
                # Linhas unidas por "\n\n": a i-esima (0-based) cai na linha 2i+1.
                book.section_lines.append(2 * len(lines) + 1)
            lines.extend(chapter)

    book.paragraphs = sum(1 for line in lines if not line.startswith("#"))
    if not lines:
        raise EpubError(f"No readable text found in {path}")
    book.markdown = "\n\n".join(lines) + "\n"
    return book


# ---------------------------------------------------------------------------
# Outros formatos (AZW3, MOBI, KFX sem DRM, FB2...) via Calibre
# ---------------------------------------------------------------------------

CONVERT_TIMEOUT = 600
# Container KFX cifrado pela Amazon: "\xeaDRMION\xee" no inicio do arquivo.
DRMION_MAGIC = b"\xeaDRMION\xee"


def _drm_message(path: Path) -> str:
    return (
        f"{path.name} is protected by Amazon DRM (typical of books bought on "
        "Amazon), so it can't be read. A DRM-free copy (EPUB from the publisher, "
        "Kobo, etc.) works with --book."
    )


def has_kfx_drm(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(DRMION_MAGIC)) == DRMION_MAGIC
    except OSError:
        return False


def _cache_dir() -> Path:
    from tts_md.audio.player import STATE_DIR

    return STATE_DIR / "book-cache"


def convert_to_epub(path: Path) -> Path:
    """Converte com o ebook-convert do Calibre e guarda o EPUB em cache.

    A chave do cache inclui tamanho e mtime: o mesmo livro nao e' convertido
    de novo a cada leitura, mas uma versao nova do arquivo e'.
    """
    import hashlib
    import shutil
    import subprocess

    if has_kfx_drm(path):
        raise EpubError(_drm_message(path))
    exe = shutil.which("ebook-convert")
    if exe is None:
        raise EpubError(
            f"{path.suffix or 'This'} books are converted with Calibre's ebook-convert, "
            "which was not found in PATH."
        )
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    from tts_md.audio.playlist import slugify

    target = _cache_dir() / f"{slugify(path.stem) or 'book'}-{digest}.epub"
    if target.exists() and target.stat().st_size > 0:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part.epub")
    try:
        result = subprocess.run(
            [exe, str(path), str(partial)],
            capture_output=True, text=True, timeout=CONVERT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        partial.unlink(missing_ok=True)
        raise EpubError(f"ebook-convert took more than {CONVERT_TIMEOUT}s on {path.name}") from exc

    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0 or not partial.exists():
        partial.unlink(missing_ok=True)
        if "DRMError" in output or "DRM" in output:
            raise EpubError(_drm_message(path))
        if "No plugin to handle input format" in output:
            fmt = path.suffix.lstrip(".").upper() or "this format"
            hint = " Install the 'KFX Input' plugin in Calibre." if fmt.startswith("KFX") else ""
            raise EpubError(f"Calibre can't read {fmt} files.{hint}")
        tail = [line for line in output.strip().splitlines() if line.strip()][-3:]
        raise EpubError(f"ebook-convert failed on {path.name}:\n" + "\n".join(tail))
    partial.replace(target)
    return target


def read_book(path: Path, *, lang: str = "pt-BR") -> EpubBook:
    """EPUB direto; qualquer outro formato passa antes pelo Calibre.

    Depois conserta palavras partidas por conversao ruim de PDF, se o livro
    tiver esse defeito e houver dicionario hunspell do idioma.
    """
    from tts_md.textfix import find_dictionary, repair_split_words

    if path.suffix.lower() == ".epub":
        book = read_epub(path)
    else:
        converted = convert_to_epub(path)
        book = read_epub(converted)
        if book.title == converted.stem:
            book.title = path.stem
    book.markdown, book.repaired = repair_split_words(book.markdown, find_dictionary(lang))
    return book
