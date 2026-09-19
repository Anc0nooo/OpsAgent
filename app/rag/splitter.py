"""
RAG 知识库 - 语义分块器（对应 FR-RAG-02，支持按文档类型差异化切分）

策略：
- markdown 标题（# ~ ######）划分章节，每个块记录所属章节（section）；
- SQL / 命令行代码块（``` 围栏）整体保留，绝不拆散；
- Markdown 表格（| 字段 | 类型 | … |）整块识别：
  · 一张表在阈值内 → 整块入库（表结构类阈值更大，优先"一表一块"，字段名与注释不分离）；
  · 超过阈值 → 按"行"切分，每个块重复表头（表头+分隔行），绝不从一行字段中间断开；
- 普通正文按语义边界递归切分，分隔符优先级：
  段落(\\n\\n) → 行(\\n) → 句末(。！？；) → 句中停顿(，、) → 空格 → 单字符兜底；
- 单块不超过 max_chars，相邻正文块保留 overlap 字符重叠，避免语义被硬切断；
- 短文档（正文总长 < whole_threshold）整块入库，不切分。

表结构类（doc_type="schema"）由调用方传入更大的 whole_threshold；
若文档被误标为其他类型但内容明显是表结构（looks_like_table_doc），
表格块同样按整表保护阈值处理，避免字段被切断。
"""
import re

from app.config.settings import settings

# markdown 标题行
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# 代码块围栏（```sql / ```bash 等）
_FENCE_PREFIX = "```"

# 递归分隔符优先级（从最粗语义边界到最细）
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", "、", " ", ""]

# Markdown 表格分隔行：如 |------|------| 或 ---|:---:
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
# 表结构特征关键字（表头常见列名）
_TABLE_HEADER_KEYWORDS = ("字段", "类型", "注释", "说明", "可空", "默认值", "field", "type", "comment")
# 非 schema 类型但内容像表结构时，整表保护阈值（字符）
_TABLE_WHOLE_FALLBACK = 4000


def looks_like_table_doc(text: str) -> bool:
    """
    检测文档是否为"表结构"特征：
    - 含 Markdown 表格分隔行（|---|）且表头行出现 字段/类型/注释 等关键字；或
    - 含 CREATE TABLE 建表语句。
    用于 doc_type 标注不准时仍按整表保护策略切分。
    """
    if re.search(r"CREATE\s+TABLE", text, re.IGNORECASE):
        return True
    lines = text.replace("\r\n", "\n").split("\n")
    has_sep = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("|") and "-" in s and _TABLE_SEP_RE.match(s):
            has_sep = True
            # 看上一行（表头）是否含字段/类型等关键字
            if i > 0:
                head = lines[i - 1].lower()
                if any(k in head for k in _TABLE_HEADER_KEYWORDS):
                    return True
    return has_sep


def _recursive_split(text: str, max_chars: int, seps: list[str]) -> list[str]:
    """
    按分隔符优先级递归切分：优先在粗语义边界（段落/行/句末）断开，
    单段仍超长则降级用更细分隔符，直到每段 ≤ max_chars；无分隔符时按字符硬切。
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sep = seps[0]
    if sep == "":
        # 兜底：连续超长串（无任何分隔符）按字符硬切
        return [text[i:i + max_chars] for i in range(0, len(text), max_chars) if text[i:i + max_chars].strip()]

    # 按分隔符切开并把分隔符补回各段末尾（保留标点语义）
    parts = text.split(sep)
    pieces: list[str] = []
    for i, p in enumerate(parts):
        piece = p + (sep if i < len(parts) - 1 else "")
        if piece.strip():
            pieces.append(piece)

    # 贪心累积到 max_chars，超限则落块；单块仍超长递归降级
    chunks: list[str] = []
    cur = ""
    for piece in pieces:
        if len(cur) + len(piece) <= max_chars:
            cur += piece
        else:
            if cur.strip():
                chunks.append(cur.strip())
            if len(piece) <= max_chars:
                cur = piece
            else:
                sub = _recursive_split(piece, max_chars, seps[1:])
                chunks.extend(sub[:-1])
                cur = sub[-1] if sub else ""
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def _with_overlap(chunks: list[str], overlap: int) -> list[str]:
    """给相邻块加重叠：后一块以前一块末尾最多 overlap 字作为前缀，保持语义连续。"""
    if overlap <= 0 or len(chunks) <= 1:
        return chunks
    out = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_tail = chunks[i - 1][-overlap:] if len(chunks[i - 1]) > overlap else chunks[i - 1]
        # 重叠起点尽量对齐到语义边界（换行/句末标点之后），避免从半句切入
        boundary = re.search(r"[\n。！？；.!?]", prev_tail)
        if boundary:
            prev_tail = prev_tail[boundary.start() + 1:]
        merged = (prev_tail + chunks[i]).strip()
        out.append(merged)
    return out


def _split_table(body: str, batch_size: int, keep_whole_cap: int) -> list[str]:
    """
    按 Markdown 表格切分（绝不从一行字段中间断开）：
    - 表格总长 ≤ keep_whole_cap：整张表一个块（字段名与注释始终同块）；
    - 超过：以"表头行+分隔行"为公共头，按字段行分批，每批 ≤ batch_size，
      每个块都重复表头，保证任一块单独检索时列含义完整。
    - 非标准表格（无分隔行）：阈值内整块，否则退回语义递归切分。
    """
    if len(body) <= keep_whole_cap:
        return [body]

    lines = body.replace("\r\n", "\n").split("\n")
    # 定位表头分隔行（前 8 行内）
    sep_idx = -1
    for i in range(min(8, len(lines))):
        s = lines[i].strip()
        if s.startswith("|") and "-" in s and _TABLE_SEP_RE.match(s):
            sep_idx = i
            break
    # 分隔行前至少要有表头行，后要有字段行
    if sep_idx < 1 or sep_idx >= len(lines) - 1:
        return _recursive_split(body, batch_size, _SEPARATORS)

    header_lines = lines[:sep_idx + 1]
    data_rows = [ln for ln in lines[sep_idx + 1:] if ln.strip()]
    header_str = "\n".join(header_lines)

    chunks: list[str] = []
    cur_rows: list[str] = []
    cur_len = len(header_str)

    def flush() -> None:
        if cur_rows:
            chunks.append(header_str + "\n" + "\n".join(cur_rows))

    for row in data_rows:
        add = len(row) + 1  # 含换行
        # 当前批非空且加入该行会超批大小 → 落块后另起一批（新批仍带表头）
        if cur_rows and cur_len + add > batch_size:
            flush()
            cur_rows = [row]
            cur_len = len(header_str) + add
        else:
            cur_rows.append(row)
            cur_len += add
    flush()

    # 极端情况：单行字段比批大小还大，也不拆行（保留完整字段信息）
    return chunks if chunks else [body]


def split_document(text: str, max_chars: int = 600, overlap: int = 150,
                   whole_threshold: int = 1000, doc_type: str = "other") -> list[dict]:
    """
    将文档文本切分为块列表。
    - max_chars:       正文语义切分上限 / 表格按行分批的批大小；
    - overlap:         相邻正文块重叠字符数（表格块不做字符重叠）；
    - whole_threshold: 正文/单张表短于此长度则整块入库，不切分；
    - doc_type:        文档类型（schema/guide/bug/other），表结构类整表阈值更大；
    返回: [{"text": 块正文, "section": 所属章节标题}, ...]
    """
    norm_text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 表结构特征检测：即使 doc_type 标注不准，含明显表格特征时也按整表保护处理
    table_like = doc_type == "schema" or looks_like_table_doc(norm_text)
    table_keep_cap = whole_threshold if doc_type == "schema" else (
        _TABLE_WHOLE_FALLBACK if table_like else whole_threshold
    )

    lines = norm_text.split("\n")

    # 1. 先按"代码块整体 / 章节标题 / 表格"把正文组织成结构块
    blocks: list[tuple[str, str, str]] = []  # (kind: text/code/table, section, body)
    current_section = ""
    text_buf: list[str] = []
    code_buf: list[str] = []
    table_buf: list[str] = []
    in_code = False

    def flush_text() -> None:
        body = "\n".join(text_buf).strip()
        if body:
            blocks.append(("text", current_section, body))
        text_buf.clear()

    def flush_table() -> None:
        body = "\n".join(table_buf).strip()
        if body:
            blocks.append(("table", current_section, body))
        table_buf.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(_FENCE_PREFIX):
            if in_code:
                # 代码块结束：连同收尾围栏整体落块（不拆散、不参与语义切分）
                code_buf.append(line)
                blocks.append(("code", current_section, "\n".join(code_buf).strip()))
                code_buf = []
                in_code = False
            else:
                # 代码块开始：先落已有正文/表格
                flush_text()
                flush_table()
                in_code = True
                code_buf = [line]
            continue

        if in_code:
            code_buf.append(line)
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            # 标题行：落已有正文/表格，更新章节（标题不单独成块，作为后续块的 section）
            flush_text()
            flush_table()
            current_section = heading.group(2).strip()
            continue

        if stripped.startswith("|"):
            # 连续的 Markdown 表格行：进表格缓冲（离开正文流）
            if text_buf:
                flush_text()
            table_buf.append(line)
        else:
            if table_buf:
                flush_table()
            text_buf.append(line)

    # 收尾：未闭合代码块也整体保留
    if in_code:
        blocks.append(("code", current_section, "\n".join(code_buf).strip()))
    flush_table()
    flush_text()

    # 2. 按块类型组装：代码原样；表格按整表/按行切（重复表头、不做字符重叠）；
    #    正文短于阈值整块，长则语义递归切分 + 相邻重叠
    chunks: list[dict] = []
    for kind, section, body in blocks:
        if kind == "code":
            chunks.append({"text": body, "section": section})
        elif kind == "table":
            for part in _split_table(body, batch_size=max_chars, keep_whole_cap=table_keep_cap):
                if part.strip():
                    chunks.append({"text": part.strip(), "section": section})
        else:
            if len(body) < whole_threshold:
                chunks.append({"text": body, "section": section})
            else:
                parts = _with_overlap(_recursive_split(body, max_chars, _SEPARATORS), overlap)
                for p in parts:
                    if p.strip():
                        chunks.append({"text": p.strip(), "section": section})
    return chunks


# ======================================================================
# 结构化切分（document_loader.ParsedDocument 的 Block 列表）
# 模式A（语义，不用 chunk_size）：schema 一表一块 / medical 段落切分
# 模式B（用户可调 chunk_size）：guide / bug / other 递归切分 + overlap
# ======================================================================
def _mk_chunk(text: str, section: str, blocks: list | tuple) -> dict:
    """从贡献块列表组装带位置元数据的 chunk（page 取首页，images 去重合并）"""
    text = text.strip()
    first = blocks[0]
    page = getattr(first, "page", None)
    header = getattr(first, "header", "") or ""
    footer = getattr(first, "footer", "") or ""
    imgs: list[dict] = []
    seen = set()
    for b in blocks:
        for im in getattr(b, "images", []) or []:
            k = im.get("id") or im.get("key")
            if k in seen:
                continue
            seen.add(k)
            imgs.append(im)
    return {"text": text, "section": section, "page": page,
            "header": header, "footer": footer, "images": imgs}


def _split_schema_semantic(blocks: list) -> list[dict]:
    """模式A-表结构：一张表（或一段 DDL）为最小完整单位，绝不切散字段列表"""
    chunks: list[dict] = []
    section = ""
    buffer: list = []   # 表格前的标题/短说明（表名、表注释）

    def flush_buffer() -> None:
        if buffer:
            chunks.append(_mk_chunk("\n\n".join(b.text for b in buffer), section, buffer))
            buffer.clear()

    for b in blocks:
        if b.kind == "heading":
            flush_buffer()
            section = b.text
            buffer = [b]
        elif b.kind == "table":
            # 表名/标题/说明 + 整张表 = 一个块
            merged = buffer + [b]
            chunks.append(_mk_chunk(
                ("\n\n".join(x.text for x in merged)), section, merged))
            buffer.clear()
        else:
            # 含 CREATE TABLE 的 DDL 段同样整体保留
            is_ddl = "CREATE TABLE" in b.text.upper() or b.text.lstrip().startswith("```")
            if is_ddl:
                merged = buffer + [b]
                chunks.append(_mk_chunk("\n\n".join(x.text for x in merged), section, merged))
                buffer.clear()
            else:
                buffer.append(b)
                # 兜底：连续说明文字过长先落一块（实际表结构文档几乎不会触发）
                if sum(len(x.text) for x in buffer) > settings.MEDICAL_LONG_PARAGRAPH:
                    flush_buffer()
    flush_buffer()
    return chunks


def _split_medical_semantic(blocks: list) -> list[dict]:
    """模式A-医疗文件：按段落切；段落 >2000 字按句子边界二次切；表格整块。

    表格与其前文（章节标题 + 标题后紧邻的短说明，每条 ≤150 字）合为一块，
    避免表格脱离表名/注释；长说明段落自成一块、不并入表格。
    """
    chunks: list[dict] = []
    section = ""
    heading_block = None   # 最近标题块
    notes: list = []       # 标题后紧邻的短说明（表格上文候选）
    for b in blocks:
        if b.kind == "heading":
            section = b.text
            heading_block = b
            notes = []
            continue
        if b.kind == "table":
            merged = ([heading_block] if heading_block else []) + notes + [b]
            chunks.append(_mk_chunk("\n\n".join(x.text for x in merged), section, merged))
            heading_block = None
            notes = []
            continue
        # 段落（含 OCR 段）：过长按句子边界二次切，但不做 overlap、不从句中硬切
        if len(b.text) > settings.MEDICAL_LONG_PARAGRAPH:
            parts = _recursive_split(
                b.text, settings.MEDICAL_LONG_PARAGRAPH,
                ["\n", "。", "！", "？", "；", "，", "、", " ", ""],
            )
            for p in parts:
                chunks.append(_mk_chunk(p, section, [b]))
            notes = []
        else:
            chunks.append(_mk_chunk(b.text, section, [b]))
            if len(b.text) <= 150:
                notes.append(b)
            else:
                notes = []
    return chunks


def _split_run_with_meta(blocks: list, section: str,
                         chunk_size: int, overlap: int) -> list[dict]:
    """模式B：把一段连续正文块按 chunk_size 递归切分 + overlap，并保留页码/图片映射"""
    if not blocks:
        return []
    # 拼全文并记录每块字符区间（用于把切好的 piece 映射回 block 的位置/图片）
    full = ""
    spans: list[tuple[int, int, object]] = []
    for b in blocks:
        start = len(full)
        seg = b.text if not full else "\n\n" + b.text
        full += seg
        spans.append((start, len(full), b))  # start=追加前长度，恰为本块起点

    raw_pieces = _recursive_split(full, chunk_size, _SEPARATORS)
    final_texts = _with_overlap(raw_pieces, overlap)

    chunks: list[dict] = []
    cursor = 0
    for i, piece in enumerate(raw_pieces):
        head = piece[:30]
        try:
            s = full.index(head, cursor)
        except ValueError:
            s = cursor
        e = min(s + len(piece), len(full))
        cursor = max(cursor, s)
        contrib = [b for (bs, be, b) in spans if be > s and bs < e]
        if not contrib:
            contrib = blocks[:1]
        chunks.append(_mk_chunk(final_texts[i], section, contrib))
    return chunks


def _split_mode_b(blocks: list, chunk_size: int, overlap: int) -> list[dict]:
    """模式B：guide/bug/other —— 标题分区，正文按用户参数切，表格按行分批不拆行"""
    chunks: list[dict] = []
    section = ""
    run: list = []

    def flush_run() -> None:
        if run:
            chunks.extend(_split_run_with_meta(run, section, chunk_size, overlap))
            run.clear()

    for b in blocks:
        if b.kind == "heading":
            flush_run()
            section = b.text
            # 标题作为新 run 的起始上下文（随首块一起进正文）
            run = [b]
        elif b.kind == "table":
            flush_run()
            # 表格 ≤2*chunk_size 整块保留；超过按行分批，每批重复表头
            for part in _split_table(b.text, batch_size=chunk_size,
                                     keep_whole_cap=chunk_size * 2):
                chunks.append(_mk_chunk(part, section, [b]))
        else:
            # 单块超长先在块内递归切，避免跨页内容被错误并到同一 piece
            if len(b.text) > chunk_size:
                sub_parts = _recursive_split(b.text, chunk_size, _SEPARATORS)
                # 图片只随首片，其余分片不重复携带
                pseudo = [
                    _pseudo_block(p, b, carry_images=(i == 0))
                    for i, p in enumerate(sub_parts)
                ]
                if run:
                    # 已累积的标题/短块不要单独成碎块：文本与图片并入首片
                    first = pseudo[0]
                    first.text = "\n\n".join(x.text for x in [*run, first] if x.text)
                    for x in run:
                        for im in getattr(x, "images", []) or []:
                            if im not in first.images:
                                first.images.append(im)
                    run.clear()
                chunks.extend(_split_run_with_meta(pseudo, section, chunk_size, overlap))
            else:
                run.append(b)
    flush_run()
    return chunks


def _pseudo_block(text: str, ref: object, carry_images: bool = False) -> object:
    """块内二次切分后的虚拟块：继承原块页码/页眉页脚；图片仅首片携带"""
    from app.rag.document_loader import Block
    return Block(kind=ref.kind, text=text, page=getattr(ref, "page", None),
                 images=getattr(ref, "images", []) if carry_images else [],
                 header=getattr(ref, "header", ""), footer=getattr(ref, "footer", ""))


def split_structured(blocks: list, doc_type: str,
                     chunk_size: int, overlap: int) -> list[dict]:
    """
    结构化块 → chunk dict（含 text/section/page/header/footer/images）。
    - schema / medical：模式A 语义切分（忽略 chunk_size/overlap）；
    - guide / bug / other：模式B，chunk_size/overlap 由每用户设置决定。
    """
    if doc_type == "schema":
        return _split_schema_semantic(blocks)
    if doc_type == "medical":
        return _split_medical_semantic(blocks)
    return _split_mode_b(blocks, chunk_size, overlap)
