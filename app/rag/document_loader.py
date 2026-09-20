"""
RAG 知识库 - 结构化文档加载器

职责：把上传的 PDF / DOCX / TXT(MD) 解析为带位置信息的结构化块，供语义切分器使用：
- 文本块（heading/paragraph/ocr）、表格块（Markdown 文本，行列结构完整）
- 内嵌图片提取落盘 + 页码 + bbox，可选 qwen-vl OCR（ocr_fn 由服务层注入，失败跳过）
- 扫描件 PDF（整页无文本层）整页渲染为图片走 OCR
- 页眉/页脚提取（PDF 取上下固定边距区域文字；Word 取 section.header/footer）

加载器不依赖数据库：图片先落盘到 images/user_{uid}/doc_{did}/，返回 image_files 清单，
由服务层写 kb_images 表拿到 image_id，再回填到 chunk 元数据。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# OCR 回调：(图片字节, mime) -> 识别文字（无文字返回 ""）；加载器不关心用哪个模型
OcrFn = Callable[[bytes, str], str]


# ----------------------------------------------------------------------
# 物理行合并（PDF/Word 提取的换行是排版换行，不是段落边界）
# ----------------------------------------------------------------------
_CJK_RANGES = (("\u4e00", "\u9fff"), ("\u3000", "\u303f"), ("\uff00", "\uffef"))


def _is_cjk(ch: str) -> bool:
    """CJK 汉字 / 中文标点 / 全角字符"""
    return any(lo <= ch <= hi for lo, hi in _CJK_RANGES)


def _join_text(a: str, b: str) -> str:
    """拼接两段文本：CJK（含中文标点）或数字边界直接相连，仅英文字母间补空格"""
    a, b = a.rstrip(), b.lstrip()
    if not a:
        return b
    if not b:
        return a
    if _is_cjk(a[-1]) or _is_cjk(b[0]):
        return a + b
    if a[-1].isascii() and a[-1].isalpha() and b[0].isascii() and b[0].isalpha():
        return a + " " + b
    return a + b


def _join_phys_lines(text: str) -> str:
    """块内物理换行合并成完整段落（断行的句子接回一句）"""
    out = ""
    for ln in text.split("\n"):
        ln = ln.strip()
        if ln:
            out = _join_text(out, ln)
    return out


# PDF 提取文本中因字形间隙被插入的多余空格：
# CJK-CJK / 数字-数字 / CJK-数字 之间不应有空格（英文字母间的空格保留）
_PDF_SPACE_RE = re.compile(
    r"(?<=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])\s+(?=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])"
    r"|(?<=\d)\s+(?=\d)"
    r"|(?<=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])\s+(?=\d)"
    r"|(?<=\d)\s+(?=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])")


def _clean_pdf_spaces(text: str) -> str:
    """清理 PDF 文本提取产生的中文/数字间多余空格"""
    return _PDF_SPACE_RE.sub("", text)


# 段落续行起始特征：跨页合并时避免把新段落/标题并进上一段
_CONT_START_RE = re.compile(
    r"^(第[一二三四五六七八九十百\d]+[章节条款部分篇]"
    r"|[（(][一二三四五六七八九十\d]+[）)、．.]"
    r"|[一二三四五六七八九十]+、"
    r"|\d+(\.\d+)*[、.\s]"
    r"|[•·●■□▶]\s)")


def _merge_cross_page_paragraphs(blocks: list) -> list:
    """跨页段落续行合并：上一页末段无句末标点且下页首段非新段落特征 → 并回一段"""
    if not blocks:
        return blocks
    out = [blocks[0]]
    for b in blocks[1:]:
        prev = out[-1]
        if (b.kind == "paragraph" and prev.kind == "paragraph"
                and b.page is not None and prev.page is not None
                and b.page == prev.page + 1
                and prev.text and prev.text[-1] not in "。！？…"
                and not _CONT_START_RE.match(b.text)):
            prev.text = _join_text(prev.text, b.text)
            prev.images.extend(b.images)
        else:
            out.append(b)
    return out


def _same_pdf_para(prev: list, cur: list, gap_thr: float) -> bool:
    """两个 PDF 文字块是否属于同一段落（同页相邻块合并依据）

    - 同一行被拆开的片段（垂直范围重叠，常见于中英文/字体切换）：无条件同段；
    - 相邻行：垂直间距 ≤ 行距阈值 且 水平范围重叠（同栏）→ 同段。
    """
    if prev[5] != "paragraph" or cur[5] != "paragraph":
        return False
    px0, py0, px1, py1 = prev[:4]
    cx0, cy0, cx1, cy1 = cur[:4]
    # 同行碎片：垂直投影重叠超过较小块高的一半
    v_overlap = min(py1, cy1) - max(py0, cy0)
    ph_h, ch_h = py1 - py0, cy1 - cy0
    if ph_h > 0 and ch_h > 0 and v_overlap > 0.5 * min(ph_h, ch_h):
        return True
    # 相邻行：间距小 + 同栏
    if cy0 - py1 > gap_thr:
        return False
    x_overlap = min(px1, cx1) - max(px0, cx0)
    return x_overlap > 0.3 * min(px1 - px0, cx1 - cx0)


@dataclass
class Block:
    """结构化块"""
    kind: str                 # heading / paragraph / table / ocr
    text: str
    page: int | None = None   # 页码（从 1 开始；txt/docx 为 None）
    images: list[dict] = field(default_factory=list)  # [{key, page, bbox, ocr_text}]
    header: str = ""
    footer: str = ""


@dataclass
class ImageFile:
    """已落盘图片（待服务层登记 kb_images 表）"""
    key: str           # 块内引用的稳定键（文件名去扩展）
    rel_path: str      # 相对 DATA_DIR 的路径
    page: int | None
    bbox: str          # x0,y0,x1,y1
    ocr_text: str
    content_type: str


@dataclass
class ParsedDocument:
    """结构化解析结果"""
    kind: str                       # pdf / docx / text
    blocks: list[Block]
    image_files: list[ImageFile] = field(default_factory=list)

    def to_cache_json(self) -> dict:
        """重建索引缓存（图片已登记后调用，images 内带 image id）"""
        return {
            "kind": self.kind,
            "blocks": [
                {
                    "kind": b.kind, "text": b.text, "page": b.page,
                    "header": b.header, "footer": b.footer,
                    "images": b.images,
                }
                for b in self.blocks
            ],
        }

    @classmethod
    def from_cache_json(cls, data: dict) -> "ParsedDocument":
        """从缓存恢复（重建索引用，OCR/图片不重跑）"""
        blocks = [
            Block(
                kind=b["kind"], text=b["text"], page=b.get("page"),
                images=b.get("images") or [], header=b.get("header", ""), footer=b.get("footer", ""),
            )
            for b in data.get("blocks", [])
        ]
        return cls(kind=data.get("kind", "text"), blocks=blocks, image_files=[])


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def _table_to_markdown(rows: list[list[str]]) -> str:
    """二维单元格 → Markdown 表格（首行表头；转义竖线与换行）"""
    clean: list[list[str]] = []
    for row in rows:
        clean.append([(c or "").replace("|", "\\|").replace("\n", " ").strip() for c in row])
    clean = [r for r in clean if any(c for c in r)]
    if not clean:
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    head = clean[0]
    lines = [
        "| " + " | ".join(head) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for r in clean[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


# ======================================================================
# PDF
# ======================================================================
def _extract_pdf(data: bytes, img_dir: Path, rel_dir: str,
                 ocr_fn: OcrFn | None) -> ParsedDocument:
    import pymupdf

    from app.config.settings import settings

    img_dir.mkdir(parents=True, exist_ok=True)
    blocks: list[Block] = []
    image_files: list[ImageFile] = []
    ocr_count = 0

    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        for pno in range(pdf.page_count):
            page = pdf[pno]
            page_no = pno + 1
            ph = page.rect.height
            header_text, footer_text = "", ""

            # ---- 扫描件：整页几乎无文本层 → 渲染整页走 OCR ----
            plain_text = page.get_text().strip()
            if len(plain_text) < settings.SCANNED_PAGE_MIN_CHARS:
                if ocr_fn is not None:
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                    img_bytes = pix.tobytes("png")
                    ocr_text = _safe_ocr(ocr_fn, img_bytes, "image/png")
                    if ocr_text:
                        blocks.append(Block(
                            kind="ocr", text=f"[图片OCR-第{page_no}页扫描件]\n{ocr_text}",
                            page=page_no,
                        ))
                        ocr_count += 1
                continue

            # ---- 表格区域（find_tables），命中区域内的文字块跳过，避免与表格文本重复 ----
            table_rects = []
            try:
                tabs = page.find_tables()
                table_rects = [t.bbox for t in tabs.tables] if tabs and tabs.tables else []
            except Exception as e:  # noqa: BLE001
                logger.debug("PDF 表格提取失败 page=%s: %s", page_no, e)
                tabs = None

            def in_table(x0: float, y0: float, x1: float, y1: float) -> bool:
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                return any(rx0 <= cx <= rx1 and ry0 <= cy <= ry1
                           for rx0, ry0, rx1, ry1 in table_rects)

            # ---- 文字块（dict 模式：取字号识别标题；blocks 模式位置更稳，用 blocks）----
            raw_blocks = page.get_text("blocks") or []
            # 字号（用于标题启发式）：dict blocks 取每块最大字号
            sizes = []
            try:
                d = page.get_text("dict")
                for db_ in d.get("blocks", []):
                    for ln in db_.get("lines", []):
                        for sp in ln.get("spans", []):
                            if sp.get("text", "").strip():
                                sizes.append(float(sp.get("size", 0)))
            except Exception:  # noqa: BLE001
                pass
            median_size = (sorted(sizes)[len(sizes) // 2]) if sizes else 12.0
            big_size = median_size * 1.15 if sizes else 999.0

            # ---- 文字块收集（含 bbox 与标题判定；供同段落碎片合并）----
            items: list[list] = []  # [x0, y0, x1, y1, text, kind]
            for item in raw_blocks:
                x0, y0, x1, y1, txt = item[0], item[1], item[2], item[3], item[4]
                btype = item[6] if len(item) > 6 else 0
                body = _join_phys_lines(txt)  # 块内物理换行 → 完整段落
                if btype != 0 or not body:
                    continue
                if in_table(x0, y0, x1, y1):
                    continue
                # 页眉/页脚：上下 8% 边距区域（不进正文）
                if y1 <= ph * 0.08:
                    header_text = (header_text + " " + body).strip()
                    continue
                if y0 >= ph * 0.92:
                    footer_text = (footer_text + " " + body).strip()
                    continue
                kind = "heading" if len(body) <= 40 and _block_max_size(page, x0, y0, x1, y1) >= big_size else "paragraph"
                items.append([x0, y0, x1, y1, body, kind])

            # ---- 同段落碎片合并：不少 PDF 把一行/一个字体片段拆成独立 block ----
            para_gap = max(6.0, median_size * 0.7)
            merged_items: list[list] = []
            for it in items:
                if merged_items and _same_pdf_para(merged_items[-1], it, para_gap):
                    m = merged_items[-1]
                    m[0], m[1] = min(m[0], it[0]), min(m[1], it[1])
                    m[2], m[3] = max(m[2], it[2]), max(m[3], it[3])
                    m[4] = _join_text(m[4], it[4])
                else:
                    merged_items.append(list(it))
            for x0, y0, x1, y1, body, kind in merged_items:
                blocks.append(Block(kind=kind, text=_clean_pdf_spaces(body), page=page_no,
                                    header=header_text, footer=footer_text))

            # ---- 表格 → Markdown（排在文字块之后、按 y0 近似插入位置）----
            if tabs and getattr(tabs, "tables", None):
                for t in tabs.tables:
                    try:
                        md = _table_to_markdown(t.extract())
                    except Exception:  # noqa: BLE001
                        continue
                    if md:
                        blocks.append(Block(kind="table", text=md, page=page_no,
                                            header=header_text, footer=footer_text))

            # ---- 内嵌图片：提取落盘 + OCR（按 xref 去重，跳过图标）----
            seen_xref: set[int] = set()
            for info in page.get_images(full=True):
                xref = info[0]
                if xref in seen_xref:
                    continue
                seen_xref.add(xref)
                if ocr_count >= settings.OCR_MAX_IMAGES_PER_DOC:
                    ocr_fn = None  # 达上限：后续只存图不 OCR
                try:
                    extracted = pdf.extract_image(xref)
                    img_bytes = extracted["image"]
                    ext = (extracted.get("ext") or "png").lower()
                except Exception:  # noqa: BLE001
                    continue
                if len(img_bytes) < 2 * 1024:  # <2KB 多为像素点/分隔图
                    continue
                rects = page.get_image_rects(xref)
                rect = rects[0] if rects else None
                if rect is not None and (rect.width < settings.OCR_MIN_IMAGE_PX
                                         or rect.height < settings.OCR_MIN_IMAGE_PX):
                    continue
                bbox = f"{rect.x0:.0f},{rect.y0:.0f},{rect.x1:.0f},{rect.y1:.0f}" if rect else ""
                key = f"p{page_no}_x{xref}"
                rel_path = f"{rel_dir}/{key}.{ext}"
                (img_dir / f"{key}.{ext}").write_bytes(img_bytes)
                ocr_text = _safe_ocr(ocr_fn, img_bytes, f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}") if ocr_fn else ""
                if ocr_text:
                    ocr_count += 1
                image_files.append(ImageFile(key, rel_path, page_no, bbox, ocr_text,
                                             f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"))
                img_ref = {"key": key, "page": page_no, "bbox": bbox, "ocr_text": ocr_text}
                # OCR 文字作为独立块（与原图绑定）；无 OCR 文字则把图片挂到同页最后一个正文块
                if ocr_text:
                    blocks.append(Block(kind="ocr", text=f"[图片OCR]\n{ocr_text}", page=page_no,
                                        images=[img_ref], header=header_text, footer=footer_text))
                elif blocks and blocks[-1].page == page_no:
                    blocks[-1].images.append(img_ref)

    # 跨页段落续行合并（段落被分页打断的场景）
    return ParsedDocument(kind="pdf", blocks=_merge_cross_page_paragraphs(blocks),
                          image_files=image_files)


def _block_max_size(page, x0: float, y0: float, x1: float, y1: float) -> float:
    """取与给定 bbox 相交的 span 最大字号（标题启发式；失败返回 0）"""
    try:
        d = page.get_text("dict")
        best = 0.0
        for db_ in d.get("blocks", []):
            for ln in db_.get("lines", []):
                lbb = ln.get("bbox")
                if lbb and (lbb[2] < x0 - 2 or lbb[0] > x1 + 2 or lbb[3] < y0 - 2 or lbb[1] > y1 + 2):
                    continue
                for sp in ln.get("spans", []):
                    if sp.get("text", "").strip():
                        best = max(best, float(sp.get("size", 0)))
        return best
    except Exception:  # noqa: BLE001
        return 0.0


def _safe_ocr(ocr_fn: OcrFn | None, img_bytes: bytes, mime: str) -> str:
    """OCR 失败/无文字一律返回空串，绝不阻断入库"""
    if ocr_fn is None:
        return ""
    try:
        return (ocr_fn(img_bytes, mime) or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("OCR 识别失败，跳过文字仅保留图片: %s", e)
        return ""


# ======================================================================
# DOCX
# ======================================================================
def _extract_docx(data: bytes, img_dir: Path, rel_dir: str,
                  ocr_fn: OcrFn | None) -> ParsedDocument:
    import io

    from docx import Document as _Docx
    from docx.oxml.ns import qn
    from docx.table import Table as _Table
    from docx.text.paragraph import Paragraph as _Paragraph

    doc = _Docx(io.BytesIO(data))
    img_dir.mkdir(parents=True, exist_ok=True)
    blocks: list[Block] = []
    image_files: list[ImageFile] = []

    # 页眉页脚（Word 按 section；首页/奇偶页简化取第一节可用文本）
    header_text = footer_text = ""
    try:
        for sec in doc.sections:
            header_text = header_text or (sec.header.text if sec.header else "").strip()
            footer_text = footer_text or (sec.footer.text if sec.footer else "").strip()
    except Exception:  # noqa: BLE001
        pass

    def paragraph_images(p: _Paragraph) -> list[dict]:
        refs: list[dict] = []
        try:
            blips = p._p.findall(".//" + qn("a:blip"))
        except Exception:  # noqa: BLE001
            return refs
        for blip in blips:
            rid = blip.get(qn("r:embed"))
            if not rid or rid not in doc.part.related_parts:
                continue
            part = doc.part.related_parts[rid]
            blob = part.blob
            if len(blob) < 2 * 1024:
                continue
            ctype = part.content_type or "image/png"
            ext = "png" if "png" in ctype else ("jpeg" if "jpeg" in ctype or "jpg" in ctype else "png")
            key = f"img_{len(image_files) + 1}"
            rel_path = f"{rel_dir}/{key}.{ext}"
            (img_dir / f"{key}.{ext}").write_bytes(blob)
            ocr_text = _safe_ocr(ocr_fn, blob, ctype) if ocr_fn else ""
            image_files.append(ImageFile(key, rel_path, None, "", ocr_text, ctype))
            refs.append({"key": key, "page": None, "bbox": "", "ocr_text": ocr_text})
        return refs

    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            p = _Paragraph(child, doc)
            imgs = paragraph_images(p)
            text = p.text.strip()
            style = (p.style.name or "") if p.style else ""
            if text:
                kind = "heading" if style.lower().startswith("heading") or style.startswith("标题") else "paragraph"
                # 段内软换行（w:br）合并成完整段落
                if kind == "paragraph":
                    text = _join_phys_lines(text)
                blocks.append(Block(kind=kind, text=text, header=header_text, footer=footer_text, images=imgs))
            elif imgs:
                # 纯图片段落：有 OCR 文字则成块，否则挂到上一块
                for ref in imgs:
                    if ref["ocr_text"]:
                        blocks.append(Block(kind="ocr", text=f"[图片OCR]\n{ref['ocr_text']}",
                                            images=[ref], header=header_text, footer=footer_text))
                    elif blocks:
                        blocks[-1].images.append(ref)
        elif child.tag == qn("w:tbl"):
            t = _Table(child, doc)
            md = _table_to_markdown([[c.text for c in row.cells] for row in t.rows])
            if md:
                blocks.append(Block(kind="table", text=md, header=header_text, footer=footer_text))

    return ParsedDocument(kind="docx", blocks=blocks, image_files=image_files)


# ======================================================================
# TXT / MD（纯文本：段落 + Markdown 标题/表格，无页码无图片）
# ======================================================================
def _extract_text(data: bytes, filename: str) -> ParsedDocument:
    text = data.decode("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[Block] = []
    lower = filename.lower()
    is_md = lower.endswith((".md", ".markdown"))
    is_sql = lower.endswith(".sql")
    # 空行分段；段内首行若为标题则标记
    for raw in text.split("\n\n"):
        body = raw.strip()
        if not body:
            continue
        if is_md and body.lstrip("#").strip() and body.lstrip().startswith("#"):
            first_line, _, rest = body.partition("\n")
            blocks.append(Block(kind="heading", text=first_line.lstrip("# ").strip()))
            if rest.strip():
                body = rest.strip()
            else:
                continue
        # 段内物理换行合并（SQL/含 Markdown 表格行的段落保留原换行）
        if is_sql or any(ln.lstrip().startswith("|") for ln in body.split("\n")):
            blocks.append(Block(kind="paragraph", text=body))
        else:
            blocks.append(Block(kind="paragraph", text=_join_phys_lines(body)))
    return ParsedDocument(kind="text", blocks=blocks)


# ======================================================================
# 统一入口
# ======================================================================
def parse_file(data: bytes, filename: str, user_id: int, doc_id: int,
               ocr_fn: OcrFn | None = None) -> ParsedDocument:
    """按扩展名解析上传文件。rel_dir 为相对 DATA_DIR 的图片目录。"""
    from app.config.settings import settings

    lower = filename.lower()
    rel_dir = f"images/user_{user_id}/doc_{doc_id}"
    img_dir = settings.IMAGES_DIR / f"user_{user_id}" / f"doc_{doc_id}"

    if lower.endswith(".pdf"):
        return _extract_pdf(data, img_dir, rel_dir, ocr_fn)
    if lower.endswith(".docx"):
        return _extract_docx(data, img_dir, rel_dir, ocr_fn)
    if lower.endswith((".txt", ".md", ".markdown", ".sql")):
        return _extract_text(data, filename)
    raise ValueError(f"不支持的文件类型: {filename}（支持 txt/md/sql/pdf/docx）")
