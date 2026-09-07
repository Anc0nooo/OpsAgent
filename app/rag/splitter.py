"""
RAG 知识库 - 语义分块器（对应 FR-RAG-02）

策略：
- markdown 标题（# ~ ######）划分章节，每个块记录所属章节（section）；
- SQL / 命令行代码块（``` 围栏）整体保留，绝不拆散；
- 普通正文按语义边界递归切分，分隔符优先级：
  段落(\\n\\n) → 行(\\n) → 句末(。！？；) → 句中停顿(，、) → 空格 → 单字符兜底；
- 单块不超过 max_chars，相邻块保留 overlap 字符重叠，避免语义被硬切断；
- 短文档（正文总长 < whole_threshold，默认 1000 字）整块入库，不切分。
"""
import re

# markdown 标题行
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# 代码块围栏（```sql / ```bash 等）
_FENCE_PREFIX = "```"

# 递归分隔符优先级（从最粗语义边界到最细）
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", "、", " ", ""]


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


def split_document(text: str, max_chars: int = 600, overlap: int = 150,
                   whole_threshold: int = 1000) -> list[dict]:
    """
    将文档文本切分为块列表。
    - max_chars:       单块最大字符数（正文语义切分上限）；
    - overlap:         相邻块重叠字符数；
    - whole_threshold: 正文短于此长度则整块入库，不切分；
    返回: [{"text": 块正文, "section": 所属章节标题}, ...]
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # 1. 先按"代码块整体 / 章节标题"把正文组织成结构块
    blocks: list[tuple[str, str, str]] = []  # (kind: text/code, section, body)
    current_section = ""
    text_buf: list[str] = []
    code_buf: list[str] = []
    in_code = False

    def flush_text() -> None:
        body = "\n".join(text_buf).strip()
        if body:
            blocks.append(("text", current_section, body))
        text_buf.clear()

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
                # 代码块开始：先落已有正文
                flush_text()
                in_code = True
                code_buf = [line]
            continue

        if in_code:
            code_buf.append(line)
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            # 标题行：落已有正文，更新当前章节（标题不单独成块，作为后续块的 section）
            flush_text()
            current_section = heading.group(2).strip()
            continue

        text_buf.append(line)

    # 收尾：未闭合代码块也整体保留
    if in_code:
        blocks.append(("code", current_section, "\n".join(code_buf).strip()))
    flush_text()

    # 2. 正文块：短文档（< whole_threshold）整块，长则语义递归切分 + 相邻重叠；代码块原样保留
    chunks: list[dict] = []
    for kind, section, body in blocks:
        if kind == "code":
            chunks.append({"text": body, "section": section})
            continue
        if len(body) < whole_threshold:
            chunks.append({"text": body, "section": section})
        else:
            parts = _with_overlap(_recursive_split(body, max_chars, _SEPARATORS), overlap)
            for p in parts:
                if p.strip():
                    chunks.append({"text": p.strip(), "section": section})
    return chunks
