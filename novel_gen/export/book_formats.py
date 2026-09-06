# -*- coding: utf-8 -*-
"""
多格式导出：epub / docx。

- epub：用 ebooklib 组装标准 EPUB 3 文件，含书名/简介/目录/章节/封面。
- docx：用 python-docx 生成 Word 文档，标题 + 简介 + 目录 + 逐章正文。

与 txt 导出一致：成品主输出到 output/ 文件夹，项目目录保留一份副本。
"""
from __future__ import annotations

from pathlib import Path

from ebooklib import epub
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from ..store.project import ProjectStore, _safe_name


def _collect(store: ProjectStore):
    """读取标题/简介/章节等公共材料。"""
    cfg = store.load_config()
    outline = store.load_outline()
    chapters = store.list_chapters()
    if not chapters:
        raise RuntimeError("没有已生成的章节，无法导出")
    return cfg, outline, chapters


def _meta_summary(outline, intro: str) -> str:
    return (outline.intro if outline and outline.intro else "").strip() or "AI 自动生成小说。"


def export_epub(store: ProjectStore) -> Path:
    """导出 EPUB 3 电子书。返回 output/ 下的最终路径。"""
    cfg, outline, chapters = _collect(store)
    title = cfg.effective_title()
    intro = _meta_summary(outline, "")

    book = epub.EpubBook()
    book.set_identifier(f"novel-{store.project_id}")
    book.set_title(title)
    book.set_language("zh")
    book.add_author("AI 自动生成")
    book.add_metadata("DC", "description", intro)

    # 简介页
    intro_page = epub.EpubHtml(title="简介", file_name="intro.xhtml", lang="zh")
    intro_page.content = (
        "<h1>{t}</h1><p><i>作者：AI 自动生成</i></p><hr/>"
        "<h2>简介</h2><p>{intro}</p>"
    ).format(t=_xml_escape(title), intro=_xml_escape(intro))
    book.add_item(intro_page)

    # 逐章
    toc_items = [intro_page]
    for ch in chapters:
        content = _xml_escape(ch.content).replace("\n", "<br/>")
        html = epub.EpubHtml(
            title=f"第{ch.no}章 {ch.title}", file_name=f"ch{ch.no:03d}.xhtml", lang="zh"
        )
        html.content = (
            "<h2>{t}</h2><div>{body}</div>"
        ).format(t=_xml_escape(f"第{ch.no}章 {ch.title}".rstrip()), body=content)
        book.add_item(html)
        toc_items.append(html)

    book.toc = tuple(toc_items)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + toc_items

    filename = f"{_safe_name(title)}.epub"
    out = _write_outputs(store, filename, book)
    return out


def _epub_bytes(book) -> bytes:
    import io
    buf = io.BytesIO()
    epub.write_epub(buf, book)
    return buf.getvalue()


def _write_outputs(store: ProjectStore, filename: str, payload) -> Path:
    """统一落盘：项目目录副本 + output/ 主输出。payload 为 bytes 或可 write_epub 对象。"""
    cfg = store.load_config()
    output_dir = store.dir.parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    proj_path = store.dir / filename
    final_path = output_dir / filename

    if isinstance(payload, bytes):
        proj_path.write_bytes(payload)
        final_path.write_bytes(payload)
    else:
        # ebooklib 对象：写临时文件再复制
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
            epub.write_epub(tmp.name, payload)
            tmp_path = Path(tmp.name)
        proj_path.write_bytes(tmp_path.read_bytes())
        final_path.write_bytes(tmp_path.read_bytes())
        tmp_path.unlink(missing_ok=True)
    return final_path


def export_docx(store: ProjectStore) -> Path:
    """导出 Word 文档（.docx）。返回 output/ 下的最终路径。"""
    cfg, outline, chapters = _collect(store)
    title = cfg.effective_title()
    intro = _meta_summary(outline, "")

    doc = Document()
    # 全局中文字体
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    style.font.size = Pt(12)

    # 标题
    h = doc.add_heading(title, level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in h.runs:
        run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = meta.add_run("作者：AI 自动生成　|　题材：" + cfg.genre)
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    if intro:
        doc.add_heading("简介", level=1)
        doc.add_paragraph(intro)

    doc.add_heading("目录", level=1)
    for ch in chapters:
        doc.add_paragraph(f"第{ch.no}章 {ch.title}".rstrip())

    # 分页 + 正文
    doc.add_page_break()
    for ch in chapters:
        doc.add_heading(f"第{ch.no}章 {ch.title}".rstrip(), level=1)
        for para in ch.content.split("\n"):
            line = para.strip()
            if not line:
                continue
            p = doc.add_paragraph(line)
            p.paragraph_format.first_line_indent = Pt(24)
            p.paragraph_format.line_spacing = 1.6

    filename = f"{_safe_name(title)}.docx"
    import io
    buf = io.BytesIO()
    doc.save(buf)
    payload = buf.getvalue()

    output_dir = store.dir.parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    proj_path = store.dir / filename
    final_path = output_dir / filename
    proj_path.write_bytes(payload)
    final_path.write_bytes(payload)
    return final_path


def _xml_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
