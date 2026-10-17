"""Corpus ingestion: sources.json → ResearchSource rows + embedded ResearchChunk rows.

Loaders (each returns ``list[Section]`` = heading + paragraphs):
- JATS XML (Europe PMC / PMC full text): title, abstract, body sections with headings
  (nested headings joined with " > "), figure captions, tables flattened to
  "cell | cell" rows; ``<back>`` (references, acknowledgements) is dropped.
- PDF (pypdf text): one section per page ("p. N"); lines re-joined into paragraphs;
  heuristic reference skipping (from a "References" line until the next "CHAPTER n").
- HTML (stdlib ``html.parser``): drops script/style/nav/header/footer/forms; keeps
  headings (as section titles) and block text. Uses ``<main>`` only when present.
- Notes fallback for ``file: null``: ``<research dir>/notes/<id>.md`` if present,
  else a short factual summary generated from the sources.json fields.

Corpus hygiene (applied to every loader in :func:`build_parents`): back-matter sections
(references, acknowledgements, funding, disclosures, consent / data-availability statements,
keyword lines, …; see :func:`is_skipped_heading`) are dropped, and the corpus is English-only:
paragraphs detected as Japanese are dropped.

Parent/child chunking (corpus v3, GymSync design): every section becomes a ``ResearchParent``
(full section text, what the LLM reads after expansion); its text is split into ~380-word child
``ResearchChunk`` rows with ~60-word sentence overlap (:func:`app.rag.chunking.split_section`),
each prefixed with ``"[Title (Year)] Section: …\\n"`` so the excerpt is self-describing in prompts
and the title/section words are searchable. Only children are embedded.

Idempotent: a source is skipped when its chunks were built by the same embedding provider and
``settings.rag_corpus_version`` with parents present; otherwise (or with ``force``) it is rebuilt.
With ``prune``, curated sources no longer listed in sources.json are deleted first.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.db.models import ResearchSource
from app.providers.embeddings import EmbeddingProvider
from app.rag import store
from app.rag.chunking import split_section
from app.rag.embeddings import embed

log = logging.getLogger(__name__)

DEFAULT_SOURCES_PATH = Path(__file__).resolve().parents[2] / "data" / "raw" / "research" / "sources.json"
_TITLE_MAX = 90
_WS = re.compile(r"[ \t 　]+")
_JA = re.compile(r"[぀-ヿ一-鿿]")


@dataclass
class Section:
    heading: str
    paragraphs: list[str] = field(default_factory=list)


def _clean(s: str) -> str:
    return _WS.sub(" ", s.replace("\r", "")).strip()


def detect_language(text: str) -> str:
    """'ja' when ≥10% of characters are kana/kanji, else 'en'."""
    if not text:
        return "en"
    return "ja" if len(_JA.findall(text)) / max(1, len(text)) >= 0.1 else "en"


# ================================================================ JATS XML

def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(el: ET.Element | None) -> str:
    return _clean(" ".join(t for t in el.itertext())) if el is not None else ""


def _find(el: ET.Element, name: str) -> ET.Element | None:
    for child in el.iter():
        if _local(child.tag) == name:
            return child
    return None


def _table_text(tw: ET.Element) -> str:
    parts = [p for p in (_text(_find(tw, "label")), _text(_find(tw, "caption"))) if p]
    rows = []
    for tr in tw.iter():
        if _local(tr.tag) != "tr":
            continue
        cells = [_text(c) for c in tr if _local(c.tag) in ("td", "th")]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join([" ".join(parts)] + rows).strip()


def _walk_jats(el: ET.Element, heading: str, out: list[Section]) -> None:
    """Collect paragraphs of ``el`` under ``heading``; nested <sec> become their own sections."""
    current = Section(heading)
    out.append(current)
    for child in el:
        name = _local(child.tag)
        if name in ("title", "label"):
            continue
        if name in ("sec", "boxed-text", "app"):
            title = _text(_find_direct(child, "title")) or name
            _walk_jats(child, f"{heading} > {title}" if heading else title, out)
            current = Section(heading)
            out.append(current)
        elif name == "table-wrap":
            current.paragraphs.append(_table_text(child))
        elif name in ("fig",):
            cap = " ".join(p for p in (_text(_find(child, "label")), _text(_find(child, "caption"))) if p)
            if cap:
                current.paragraphs.append(cap)
        elif name in ("list",):
            current.paragraphs.extend(_text(li) for li in child if _local(li.tag) == "list-item")
        elif name in ("ref-list", "fn-group", "ack"):
            continue
        else:
            t = _text(child)
            if t:
                current.paragraphs.append(t)


def _find_direct(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if _local(c.tag) == name), None)


def parse_jats(data: bytes | str) -> tuple[str, list[Section]]:
    """Returns ``(article_title, sections)``. Raises ``ET.ParseError`` on malformed XML."""
    raw = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    raw = re.sub(r"<!DOCTYPE[^>]*>", "", raw, count=1)
    raw = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)\w+;", " ", raw)  # stray HTML entities
    root = ET.fromstring(raw)
    title = ""
    meta = _find(root, "article-meta")
    if meta is not None:
        title = _text(_find(meta, "article-title"))
    sections: list[Section] = []
    if meta is not None:
        for abs_el in [e for e in meta.iter() if _local(e.tag) == "abstract"]:
            kind = abs_el.get("abstract-type")
            _walk_jats(abs_el, "Abstract" + (f" ({kind})" if kind else ""), sections)
    body = _find(root, "body")
    if body is not None:
        _walk_jats(body, "", sections)
    floats = _find(root, "floats-group")
    if floats is not None:
        tables = Section("Tables and figures")
        for tw in floats.iter():
            if _local(tw.tag) == "table-wrap":
                tables.paragraphs.append(_table_text(tw))
            elif _local(tw.tag) == "fig":
                cap = _text(_find(tw, "caption"))
                if cap:
                    tables.paragraphs.append(cap)
        sections.append(tables)
    for s in sections:
        if not s.heading:
            s.heading = "Body"
    return title, [s for s in sections if s.paragraphs]


# ================================================================ PDF

_REF_HEAD = re.compile(r"^\s*(references|bibliography|参考文献|文献)\s*$", re.I)
_CHAPTER = re.compile(r"^\s*(chapter|第\s*\d+\s*章)\s*\d*", re.I)


def parse_pdf(path: Path) -> list[Section]:
    """One section per page with text; lines re-joined into paragraphs."""
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(str(path))
    sections: list[Section] = []
    in_refs = False
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # pragma: no cover - broken page
            continue
        paras: list[str] = []
        buf: list[str] = []
        for line in text.replace("-\n", "").splitlines():
            line = _clean(line)
            if not line:
                continue
            if _REF_HEAD.match(line):
                in_refs = True
                continue
            if in_refs and _CHAPTER.match(line):
                in_refs = False
            if in_refs:
                continue
            buf.append(line)
            if re.search(r"[.!?。．:：]$", line) and sum(map(len, buf)) > 80:
                paras.append(_join_lines(buf))
                buf = []
        if buf:
            paras.append(_join_lines(buf))
        paras = [p for p in paras if len(p) >= 20]
        if sum(map(len, paras)) >= 50:
            sections.append(Section(f"p. {i}", paras))
    return sections


def _join_lines(lines: list[str]) -> str:
    sep = "" if detect_language("".join(lines)) == "ja" else " "
    return sep.join(lines)


# ================================================================ HTML

_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "button", "aside",
              "iframe", "template", "select", "option", "head"}
_BLOCK_TAGS = {"p", "div", "li", "td", "th", "tr", "section", "article", "br", "dd", "dt", "blockquote",
               "table", "ul", "ol", "main", "figcaption", "pre"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "track"}


class _HtmlText(HTMLParser):
    def __init__(self, main_only: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.main_only = main_only
        self.in_main = 0
        self.skip = 0
        self.buf: list[str] = []
        self.in_heading = False
        self.title = ""
        self._in_title = False
        self.sections: list[Section] = [Section("Page")]

    def _flush(self) -> None:
        text = _clean("".join(self.buf))
        self.buf = []
        if not text:
            return
        if self.in_heading:
            self.sections.append(Section(text[:120]))
        else:
            self.sections[-1].paragraphs.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag == "main":
            self.in_main += 1
        if tag in _SKIP_TAGS and tag not in _VOID:
            self.skip += 1
            return
        if tag in _HEADINGS:
            self._flush()
            self.in_heading = True
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if tag in _HEADINGS:
            self._flush()
            self.in_heading = False
        elif tag in _BLOCK_TAGS:
            self._flush()
        if tag == "main":
            self._flush()
            self.in_main = max(0, self.in_main - 1)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self.skip or (self.main_only and not self.in_main):
            return
        self.buf.append(data)


def parse_html(data: bytes | str) -> tuple[str, list[Section]]:
    """Returns ``(page_title, sections)``."""
    raw = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    p = _HtmlText(main_only=bool(re.search(r"<main[\s>]", raw, re.I)))
    p.feed(raw)
    p._flush()
    seen: set[str] = set()
    out: list[Section] = []
    for s in p.sections:
        paras = []
        for t in s.paragraphs:
            if len(t) < 3 or t in seen:
                continue
            seen.add(t)
            paras.append(t)
        if paras:
            out.append(Section(s.heading, paras))
    return _clean(p.title), out


# ================================================================ notes fallback

def notes_sections(src: dict[str, Any], research_dir: Path) -> list[Section]:
    """``notes/<id>.md`` (paragraphs split on blank lines, '#' lines become headings), else a
    generated summary of the bibliographic fields."""
    path = research_dir / "notes" / f"{src['id']}.md"
    if path.exists():
        sections: list[Section] = [Section("Notes")]
        for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")):
            block = block.strip()
            if not block:
                continue
            if block.startswith("#"):
                head, _, rest = block.partition("\n")
                sections.append(Section(head.lstrip("# ").strip()))
                block = rest.strip()
                if not block:
                    continue
            sections[-1].paragraphs.append(_clean(block.replace("\n", " ")))
        return [s for s in sections if s.paragraphs]
    fields = [
        f"{src.get('title')}.",
        f"Authors: {src['authors']}." if src.get("authors") else "",
        f"Published {src['year']} in {src.get('journal') or 'n/a'}." if src.get("year") else "",
        f"DOI: {src['doi']}." if src.get("doi") else "",
        f"Pathway: {', '.join(src.get('pathway_tags') or [])}. Kind: {src.get('kind') or 'n/a'}.",
        f"Full text: {src.get('url')}." if src.get("url") else "",
        "Only bibliographic metadata is available in this corpus; consult the original for findings.",
    ]
    return [Section("Summary (metadata only)", [" ".join(f for f in fields if f)])]


# ================================================================ chunking + ingestion

def make_prefix(title: str, year: int | None, heading: str) -> str:
    t = title if len(title) <= _TITLE_MAX else title[: _TITLE_MAX - 1].rstrip() + "…"
    return f"[{t} ({year or 'n.d.'})] Section: {heading}\n"


@dataclass
class ParentDraft:
    section: str
    text: str                                       # full section text (no prefix)
    children: list[tuple[str, dict[str, Any]]]      # [(prefixed child text, meta)]


_SKIP_HEADING = re.compile(
    r"^(?:[\dIVX]+(?:\.\d+)*\.?\s+)?(?:references?|bibliography|works cited|literature cited|"
    r"acknowledge?ments?|footnotes?|funding(?: sources?)?|sources of funding|disclosures?|"
    r"conflicts? of interest|competing interests?|declaration of interests?|author contributions?|"
    r"supplementary (?:material|materials|data|information)|supporting information|"
    r"consent statement|ethics statement|data availability(?: statement)?|data sharing(?: statement)?|"
    r"associated data|contributor information|abbreviations|keywords?)[\s.:]*$", re.I)
_SKIP_PARAGRAPH = re.compile(r"^\s*(?:key ?words|abbreviations)\s*:", re.I)


def is_skipped_heading(heading: str) -> bool:
    """True for back-matter sections (references, acknowledgements, funding, disclosures, consent,
    data availability, keywords, …).
    Checks every level of a nested ``"A > B"`` heading, so sub-sections of References go too."""
    return any(_SKIP_HEADING.match(part.strip()) for part in (heading or "").split(" > "))


def build_parents(title: str, year: int | None, sections: Iterable[Section]) -> list[ParentDraft]:
    """Sections → parents with prefixed ~380-word children (meta ``{section, lang, child_index}``;
    ``child_index`` is global within the source). Back-matter sections and non-English paragraphs
    are dropped (English-only corpus)."""
    out: list[ParentDraft] = []
    n = 0
    for s in sections:
        if is_skipped_heading(s.heading):
            continue
        text = "\n\n".join(p.strip() for p in s.paragraphs
                           if p and p.strip() and detect_language(p) == "en" and not _SKIP_PARAGRAPH.match(p))
        if not text:
            continue
        prefix = make_prefix(title, year, s.heading)
        children = []
        for body in split_section(text):
            children.append((prefix + body, {"section": s.heading, "lang": "en", "child_index": n}))
            n += 1
        if children:
            out.append(ParentDraft(s.heading, text, children))
    return out


def build_chunks(title: str, year: int | None, sections: Iterable[Section]) -> list[tuple[str, dict[str, Any]]]:
    """Flat ``[(prefixed_child_text, meta)]`` view of :func:`build_parents`."""
    return [c for p in build_parents(title, year, sections) for c in p.children]


def _corpus_version() -> str:
    from app.core.config import settings

    return settings.rag_corpus_version


def _is_current(db: Session, source_id: str, provider_name: str) -> bool:
    """Existing chunks were built by this provider + corpus version and all have parents."""
    from sqlalchemy import func, select

    from app.db.models import ResearchChunk

    rows = db.execute(select(ResearchChunk.meta, ResearchChunk.parent_id)
                      .where(ResearchChunk.source_id == source_id).limit(50)).all()
    if not rows:
        return False
    orphan = db.scalar(select(func.count(ResearchChunk.id)).where(
        ResearchChunk.source_id == source_id, ResearchChunk.parent_id.is_(None)))
    return not orphan and all((m or {}).get("embedder") == provider_name
                              and (m or {}).get("corpus_version") == _corpus_version() for m, _ in rows)


def _write_parents(db: Session, source_id: str, drafts: list[ParentDraft], provider: EmbeddingProvider | None,
                   extra_meta: dict[str, Any], start_parent: int = 0, start_chunk: int = 0) -> int:
    from app.providers.embeddings import get_embedding_provider

    provider = provider or get_embedding_provider()
    texts = [t for d in drafts for t, _ in d.children]
    if not texts:
        return 0
    vectors = embed(texts, input_type="document", provider=provider)
    metas, parent_ids = [], []
    for i, d in enumerate(drafts):
        parent = store.add_parent(db, source_id, start_parent + i, d.section, d.text)
        for _, m in d.children:
            metas.append(dict(m, **extra_meta, embedder=provider.name, corpus_version=_corpus_version()))
            parent_ids.append(parent.id)
    store.add_chunks(db, source_id, texts, vectors, metas, start_ordinal=start_chunk, parent_ids=parent_ids)
    return len(texts)


def load_sections(src: dict[str, Any], research_dir: Path) -> tuple[str, list[Section]]:
    """Dispatch on the file extension; returns ``(loader_name, sections)``."""
    fname = src.get("file")
    if not fname:
        return "notes", notes_sections(src, research_dir)
    path = research_dir / fname
    if not path.exists():
        log.warning("corpus file missing, using notes fallback", extra={"source": src["id"], "file": fname})
        return "notes", notes_sections(src, research_dir)
    ext = path.suffix.lower()
    if ext == ".xml":
        return "jats", parse_jats(path.read_bytes())[1]
    if ext == ".pdf":
        return "pdf", parse_pdf(path)
    if ext in (".html", ".htm"):
        return "html", parse_html(path.read_bytes())[1]
    if ext in (".md", ".txt"):
        text = path.read_text(encoding="utf-8")
        return "text", [Section("Text", [p for p in re.split(r"\n\s*\n", text) if p.strip()])]
    raise ValueError(f"unsupported corpus file type: {fname}")


def upsert_source(db: Session, src: dict[str, Any]) -> ResearchSource:
    row = db.get(ResearchSource, src["id"])
    if row is None:
        row = ResearchSource(id=src["id"], title=src.get("title") or src["id"])
        db.add(row)
    row.title = src.get("title") or row.title
    row.authors = src.get("authors")
    row.year = src.get("year")
    row.url = src.get("url")
    row.doi = src.get("doi")
    row.pathway_tags = list(src.get("pathway_tags") or [])
    row.kind = src.get("kind")
    db.flush()
    return row


def ingest_source(db: Session, provider: EmbeddingProvider | None, src: dict[str, Any], research_dir: Path,
                  limit_chunks: int | None = None, force: bool = False) -> dict[str, Any]:
    """Upsert one source and (re)build its chunks. Skips when chunks exist unless ``force``."""
    from app.providers.embeddings import get_embedding_provider

    provider = provider or get_embedding_provider()
    row = upsert_source(db, src)
    existing = store.chunk_count(db, row.id)
    if existing and not force and _is_current(db, row.id, provider.name):
        return {"id": row.id, "status": "skipped", "chunks": existing, "parents": store.parent_count(db, row.id)}
    store.delete_chunks(db, row.id)  # chunks + parents (also clears stale parents without chunks)
    loader, sections = load_sections(src, research_dir)
    drafts = build_parents(row.title, row.year, sections)
    if limit_chunks is not None:
        kept, left = [], limit_chunks
        for d in drafts:
            if left <= 0:
                break
            kept.append(ParentDraft(d.section, d.text, d.children[:left]))
            left -= len(kept[-1].children)
        drafts = kept
    n = _write_parents(db, row.id, drafts, provider, {"file": src.get("file"), "loader": loader})
    if not n:
        return {"id": row.id, "status": "empty", "loader": loader, "chunks": 0, "parents": 0}
    return {"id": row.id, "status": "ingested", "loader": loader, "chunks": n, "parents": len(drafts)}


def load_sources_file(sources_path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(sources_path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(s, dict) and s.get("id") for s in data):
        raise ValueError("sources.json must be a list of objects with an 'id'")
    return data


def prune_sources(db: Session, keep_ids: Iterable[str]) -> list[str]:
    """Delete curated sources (and their parents + chunks) whose id is not in ``keep_ids``.
    Ad-hoc ``kind="manual"`` sources from ``POST /api/ingest`` are kept. Commits."""
    from sqlalchemy import select

    keep = set(keep_ids)
    stale = [r.id for r in db.scalars(select(ResearchSource)) if r.id not in keep and r.kind != "manual"]
    for sid in stale:
        store.delete_chunks(db, sid)
        db.delete(db.get(ResearchSource, sid))
    if stale:
        db.commit()
        log.info("pruned research sources", extra={"sources": stale})
    return stale


def ingest_corpus(db: Session, provider: EmbeddingProvider | None, sources_path: str | Path = DEFAULT_SOURCES_PATH,
                  limit_chunks_per_source: int | None = None, force: bool = False,
                  only: Iterable[str] | None = None, prune: bool = False) -> dict[str, Any]:
    """Ingest every source in ``sources_path`` (files resolved next to it). Commits per source,
    so a failure in one source does not lose the others. Idempotent unless ``force``.
    ``prune`` first deletes curated sources that are no longer listed in sources.json, so removing
    an entry removes it from retrieval (``scripts/ingest_corpus.py`` prunes by default)."""
    path = Path(sources_path)
    only_set = set(only) if only else None
    sources = load_sources_file(path)
    pruned = prune_sources(db, (s["id"] for s in sources)) if prune else []
    results: list[dict[str, Any]] = []
    for src in sources:
        if only_set is not None and src["id"] not in only_set:
            continue
        try:
            res = ingest_source(db, provider, src, path.parent, limit_chunks_per_source, force)
            db.commit()
        except Exception as exc:  # keep going; report per source
            db.rollback()
            log.exception("ingest failed", extra={"source": src["id"]})
            res = {"id": src["id"], "status": "failed", "error": f"{type(exc).__name__}: {exc}", "chunks": 0}
        results.append(res)
    return {
        "sources": len(results),
        "ingested": sum(1 for r in results if r["status"] == "ingested"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "total_chunks": sum(r.get("chunks", 0) for r in results),
        "total_parents": sum(r.get("parents", 0) for r in results),
        "pruned": pruned,
        "per_source": results,
    }


def ingest_text(db: Session, provider: EmbeddingProvider | None, text: str, source_id: str,
                title: str | None = None, pathway_tags: list[str] | None = None, url: str | None = None,
                year: int | None = None) -> int:
    """Ad-hoc ingestion (``POST /api/ingest``): appends chunks to ``source_id`` (created if needed)."""
    row = db.get(ResearchSource, source_id)
    if row is None:
        row = ResearchSource(id=source_id, title=title or source_id, pathway_tags=list(pathway_tags or []),
                             url=url, year=year, kind="manual")
        db.add(row)
        db.flush()
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    drafts = build_parents(row.title, row.year, [Section("Text", paragraphs)])
    n_chunks = store.chunk_count(db, row.id)
    for d in drafts:  # keep child_index global within the source when appending
        d.children = [(t, dict(m, child_index=m["child_index"] + n_chunks)) for t, m in d.children]
    n = _write_parents(db, row.id, drafts, provider, {"loader": "manual"},
                       start_parent=store.parent_count(db, row.id), start_chunk=n_chunks)
    db.flush()
    return n
