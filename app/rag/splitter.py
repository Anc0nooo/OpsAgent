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
