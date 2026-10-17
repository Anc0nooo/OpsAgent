"""
RAG 知识库 - 结构化文档加载器

职责：把上传的 PDF / DOCX / TXT(MD) 解析为带位置信息的结构化块，供语义切分器使用：
- 文本块（heading/paragraph/ocr）、表格块（Markdown 文本，行列结构完整）
- 内嵌图片提取落盘 + 页码 + bbox，可选 qwen-vl OCR（ocr_fn 由服务层注入，失败跳过）
- 扫描件 PDF（整页无文本层）整页渲染为图片走 OCR
- 页眉/页脚提取（PDF 取上下固定边距区域文字；Word 取 section.header/footer）

性能设计（大文件友好）：
- PDF 逐页流式解析，页 dict（get_text("dict")）每页只解析一次并缓存（标题字号与
  页内块判定复用），避免对每个文字块重复解析整页导致 O(块数²) 卡顿；
- OCR 延迟并发：解析时仅收集 OCR 任务（图片已落盘，不驻留内存），全文收集完后
  用线程池按 OCR_CONCURRENCY 并发识别，耗时随并发数而非图片数线性放大；
- progress_fn(page_done, page_total) 逐页回调，供前端解析阶段进度展示。

加载器不依赖数据库：图片先落盘到 images/user_{uid}/doc_{did}/，返回 image_files 清单，
由服务层写 kb_images 表拿到 image_id，再回填到 chunk 元数据。
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.config.settings import settings

logger = logging.getLogger(__name__)

# OCR 回调：(图片字节, mime) -> 识别文字（无文字返回 ""）；加载器不关心用哪个模型
OcrFn = Callable[[bytes, str], str]
# 解析进度回调：(已完成页/条数, 总数) -> None
ProgressFn = Callable[[int, int], None] | None


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
# OCR 并发执行（延迟式：解析时只收集任务，全文收集完统一并发识别）
# ======================================================================
def _collect_ocr_job(jobs: list[dict], **kw) -> None:
    """登记一个 OCR 任务：
    - kind=page:     扫描件整页（渲染图已落盘，path 指向渲染 PNG）
    - kind=standalone: 独立图片（OCR 出文字会拆成独立 OCR 块，否则留在宿主块）
    - kind=inline:   文字段落内的图片（OCR 文字只回填 ImageFile，不动块结构）
    insert_pos 记录收集时的 blocks 长度，回填时按位置倒序插入保持页序。
    """
    jobs.append(kw)


def _apply_ocr_result(blocks: list, job: dict, text: str) -> None:
    """把单个 OCR 结果回填到块结构（按 insert_pos 倒序应用，保持原有页序）"""
    if job["kind"] == "page":
        if text:
            blocks.insert(job["insert_pos"], Block(
                kind="ocr", text=f"[图片OCR-第{job['page_no']}页扫描件]\n{text}",
                page=job["page_no"],
            ))
        return
    imf: ImageFile = job["image_file"]
    imf.ocr_text = text
    ref = job.get("img_ref")
    if ref is not None:
        ref["ocr_text"] = text
    if job["kind"] == "inline" or not text:
        return  # 段内图片只回填元数据；无文字的独立图保持挂载在宿主块
    # standalone + 有文字：从宿主块摘下引用，在原位置插入独立 OCR 块
    host = job.get("host")
    if host is not None and ref is not None and ref in host.images:
        host.images.remove(ref)
    blocks.insert(job["insert_pos"], Block(
        kind="ocr", text=f"[图片OCR]\n{text}", page=job["page_no"],
        images=[ref] if ref is not None else [],
        header=job.get("header", ""), footer=job.get("footer", ""),
    ))


def _run_ocr_jobs(jobs: list[dict], ocr_fn: OcrFn | None,
                  blocks: list, progress_fn: ProgressFn = None,
                  progress_base: int = 0, progress_total: int = 0) -> int:
    """
    并发执行收集好的 OCR 任务（ThreadPoolExecutor，OCR_CONCURRENCY 限流防 429）。
    全部完成后按 insert_pos 倒序回填块结构。返回成功产出文字的任务数。
    """
    if not jobs or ocr_fn is None:
        return 0

    def work(job: dict) -> str:
        try:
            img_bytes = Path(job["path"]).read_bytes()
            return _safe_ocr(ocr_fn, img_bytes, job["mime"])
        except OSError as e:
            logger.warning("OCR 图片读取失败 %s: %s", job["path"], e)
            return ""

    ok = 0
    results: list[tuple[dict, str]] = []
    with ThreadPoolExecutor(max_workers=min(settings.OCR_CONCURRENCY, len(jobs))) as ex:
        futs = {ex.submit(work, j): j for j in jobs}
        for i, fut in enumerate(as_completed(futs)):
            job = futs[fut]
            try:
                text = fut.result() or ""
            except Exception as e:  # noqa: BLE001
                logger.warning("OCR 任务执行失败（跳过该图）: %s", e)
                text = ""
            if text:
                ok += 1
            results.append((job, text))
            # OCR 阶段进度（基于收集顺序的页码区间上报）
            if progress_fn and progress_total:
                progress_fn(progress_base + i + 1, progress_total)

    # 按 insert_pos 倒序回填：靠前的插入不影响靠后任务记录的位置索引
    for job, text in sorted(results, key=lambda x: x[0]["insert_pos"], reverse=True):
        _apply_ocr_result(blocks, job, text)
    return ok


# ======================================================================
# PDF
# ======================================================================
def _init_ocr_quota() -> int:
    """读取 OCR 名额配置；获取失败降级为 0（本次跳过 OCR，仅提取原图，不阻断解析）。"""
    try:
        return int(getattr(settings, "OCR_MAX_IMAGES_PER_DOC", 0))
    except Exception as e:  # noqa: BLE001
        logger.warning("OCR 配额配置读取失败，本次解析跳过 OCR（不阻断入库）: %s", e)
        return 0


def _extract_pdf(data: bytes, img_dir: Path, rel_dir: str,
                 ocr_fn: OcrFn | None, progress_fn: ProgressFn = None) -> ParsedDocument:
    import pymupdf

    img_dir.mkdir(parents=True, exist_ok=True)
    blocks: list[Block] = []
    image_files: list[ImageFile] = []
    ocr_jobs: list[dict] = []   # 延迟 OCR 任务（页循环内只收集，最后并发执行）
    ocr_quota: int = _init_ocr_quota()  # 剩余 OCR 名额（控成本/控时长；获取失败降级为 0）

    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        total_pages = pdf.page_count
        for pno in range(total_pages):
            page = pdf[pno]
            page_no = pno + 1
            ph = page.rect.height
            header_text, footer_text = "", ""

            # ---- 页 dict 只解析一次并缓存（字号统计与块级判定复用，消除 O(n²)）----
            try:
                page_dict = page.get_text("dict")
            except Exception:  # noqa: BLE001
                page_dict = {}

            # ---- 扫描件：整页几乎无文本层 → 渲染整页落盘，稍后并发 OCR ----
            plain_text = page.get_text().strip()
            if len(plain_text) < settings.SCANNED_PAGE_MIN_CHARS:
                if ocr_fn is not None and ocr_quota and ocr_quota > 0:
                    try:
                        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                        key = f"p{page_no}_scan"
                        scan_path = img_dir / f"{key}.png"
                        scan_path.write_bytes(pix.tobytes("png"))
                        ocr_quota -= 1
                        _collect_ocr_job(
                            ocr_jobs, kind="page", page_no=page_no,
                            path=str(scan_path), mime="image/png",
                            insert_pos=len(blocks),
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("扫描页渲染失败 page=%s: %s", page_no, e)
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

            # ---- 字号统计（复用缓存 page_dict）----
            sizes = []
            try:
                for db_ in page_dict.get("blocks", []):
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
            for item in page.get_text("blocks") or []:
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
                kind = "heading" if len(body) <= 40 and _block_max_size(page_dict, x0, y0, x1, y1) >= big_size else "paragraph"
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

            # ---- 内嵌图片：提取落盘（OCR 延迟并发；按 xref 去重，跳过图标）----
            seen_xref: set[int] = set()
            for info in page.get_images(full=True):
                xref = info[0]
                if xref in seen_xref:
                    continue
                seen_xref.add(xref)
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
                mime = f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"
                imf = ImageFile(key, rel_path, page_no, bbox, "", mime)
                image_files.append(imf)
                img_ref = {"key": key, "page": page_no, "bbox": bbox, "ocr_text": ""}
                host = blocks[-1] if blocks and blocks[-1].page == page_no else None
                if ocr_fn is not None and ocr_quota and ocr_quota > 0:
                    # OCR 延迟执行：出文字拆独立 OCR 块（插回本页末尾），否则留在宿主块
                    ocr_quota -= 1
                    if host is not None:
                        host.images.append(img_ref)
                    _collect_ocr_job(
                        ocr_jobs, kind="standalone", page_no=page_no,
                        path=str(img_dir / f"{key}.{ext}"), mime=mime,
                        image_file=imf, img_ref=img_ref, host=host,
                        header=header_text, footer=footer_text,
                        insert_pos=len(blocks),
                    )
                elif host is not None:
                    host.images.append(img_ref)

            if progress_fn:
                progress_fn(page_no, total_pages)

    # ---- 统一并发 OCR（图片已落盘，任务只持有路径，不驻留内存）----
    _run_ocr_jobs(ocr_jobs, ocr_fn, blocks, progress_fn=progress_fn,
                  progress_base=total_pages, progress_total=total_pages + len(ocr_jobs))

    # 跨页段落续行合并（段落被分页打断的场景）
    return ParsedDocument(kind="pdf", blocks=_merge_cross_page_paragraphs(blocks),
                          image_files=image_files)


def _block_max_size(page_dict: dict, x0: float, y0: float, x1: float, y1: float) -> float:
    """取与给定 bbox 相交的 span 最大字号（标题启发式；复用缓存的页 dict，失败返回 0）"""
    try:
        best = 0.0
        for db_ in page_dict.get("blocks", []):
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
                  ocr_fn: OcrFn | None, progress_fn: ProgressFn = None) -> ParsedDocument:
    import io

    from docx import Document as _Docx
    from docx.oxml.ns import qn
    from docx.table import Table as _Table
    from docx.text.paragraph import Paragraph as _Paragraph

    doc = _Docx(io.BytesIO(data))
    img_dir.mkdir(parents=True, exist_ok=True)
    blocks: list[Block] = []
    image_files: list[ImageFile] = []
    ocr_jobs: list[dict] = []
    ocr_quota: int = _init_ocr_quota()

    # 页眉页脚（Word 按 section；首页/奇偶页简化取第一节可用文本）
    header_text = footer_text = ""
    try:
        for sec in doc.sections:
            header_text = header_text or (sec.header.text if sec.header else "").strip()
            footer_text = footer_text or (sec.footer.text if sec.footer else "").strip()
    except Exception:  # noqa: BLE001
        pass

    def paragraph_images(p: _Paragraph) -> list[dict]:
        """提取段内图片：落盘 + 登记 ImageFile，OCR 延迟并发（inline 任务只回填元数据）"""
        nonlocal ocr_quota  # 内部有 -= 赋值，必须声明引用外层名额计数，否则按局部变量处理触发 UnboundLocalError
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
            imf = ImageFile(key, rel_path, None, "", "", ctype)
            image_files.append(imf)
            ref = {"key": key, "page": None, "bbox": "", "ocr_text": ""}
            if ocr_fn is not None and ocr_quota > 0:
                ocr_quota -= 1
                _collect_ocr_job(
                    ocr_jobs, kind="inline", page_no=None,
                    path=str(img_dir / f"{key}.{ext}"), mime=ctype,
                    image_file=imf, img_ref=ref, host=None,
                    header=header_text, footer=footer_text,
                    insert_pos=len(blocks),
                )
            refs.append(ref)
        return refs

    body = doc.element.body
    children = list(body.iterchildren())
    for idx, child in enumerate(children):
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
                # 纯图片段落：OCR 出文字则成独立块（延迟回填），否则挂到上一块
                for ref in imgs:
                    host = blocks[-1] if blocks else None
                    if host is not None:
                        host.images.append(ref)
                    imf = next((x for x in image_files if x.key == ref["key"]), None)
                    if ocr_fn is not None and ocr_quota and ocr_quota > 0 and imf is not None:
                        ext = "jpeg" if "jpeg" in (imf.content_type or "") else "png"
                        ocr_quota -= 1
                        _collect_ocr_job(
                            ocr_jobs, kind="standalone", page_no=None,
                            path=str(img_dir / f"{ref['key']}.{ext}"), mime=imf.content_type or "image/png",
                            image_file=imf, img_ref=ref, host=host,
                            header=header_text, footer=footer_text,
                            insert_pos=len(blocks),
                        )
        elif child.tag == qn("w:tbl"):
            t = _Table(child, doc)
            md = _table_to_markdown([[c.text for c in row.cells] for row in t.rows])
            if md:
                blocks.append(Block(kind="table", text=md, header=header_text, footer=footer_text))
        if progress_fn:
            progress_fn(idx + 1, len(children))

    # ---- 统一并发 OCR ----
    _run_ocr_jobs(ocr_jobs, ocr_fn, blocks)

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
               ocr_fn: OcrFn | None = None, progress_fn: ProgressFn = None) -> ParsedDocument:
    """按扩展名解析上传文件。rel_dir 为相对 DATA_DIR 的图片目录。
    progress_fn(done, total)：PDF 按页、Word 按元素推进，供解析阶段进度展示。"""
    lower = filename.lower()
    rel_dir = f"images/user_{user_id}/doc_{doc_id}"
    img_dir = settings.IMAGES_DIR / f"user_{user_id}" / f"doc_{doc_id}"

    if lower.endswith(".pdf"):
        return _extract_pdf(data, img_dir, rel_dir, ocr_fn, progress_fn=progress_fn)
    if lower.endswith(".docx"):
        return _extract_docx(data, img_dir, rel_dir, ocr_fn, progress_fn=progress_fn)
    if lower.endswith((".txt", ".md", ".markdown", ".sql")):
        if progress_fn:
            progress_fn(1, 1)
        return _extract_text(data, filename)
    raise ValueError(f"不支持的文件类型: {filename}（支持 txt/md/sql/pdf/docx）")
