"""
RAG 知识库 - 简易 BM25 索引（内存版，Python 实现）
选型说明：个人自用数据量小（数千块内），内存 BM25 足够且无 FTS5 兼容风险。
分词策略（无第三方依赖）：
- 英文/数字/下划线连续段（表名、ORA-错误码、字段名）按整词保留 —— 保证强关键词命中；
- 中文连续段按字符 bigram 切分 —— 兼顾召回与精度。
"""
import math
import re
from collections import Counter

# 英文数字词 或 中文连续段
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-\.]+|[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """分词：英文整词小写；中文 bigram"""
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", raw):
            if len(raw) == 1:
                tokens.append(raw)
            else:
                tokens.extend(raw[i:i + 2] for i in range(len(raw) - 1))
        else:
            tokens.append(raw.lower())
    return tokens


class BM25Index:
    """BM25 内存索引，支持增量增删（启动全量构建 + 入库/删除时同步维护）"""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: dict[str, Counter] = {}      # chunk_id -> 词频
        self._doc_len: dict[str, int] = {}       # chunk_id -> 词数
        self._df: Counter = Counter()            # 词 -> 含该词文档数
        self._avg_len: float = 0.0

    # ------------------------------------------------------------------
    def _add_doc(self, cid: str, tokens: list[str]) -> None:
        tf = Counter(tokens)
        self._docs[cid] = tf
        self._doc_len[cid] = len(tokens)
        for term in tf:
            self._df[term] += 1
        self._avg_len = sum(self._doc_len.values()) / len(self._doc_len)

    def add(self, cid: str, text: str) -> None:
        """新增/替换一个块"""
        self.remove(cid)
        tokens = tokenize(text)
        if tokens:
            self._add_doc(cid, tokens)

    def remove(self, cid: str) -> None:
        """删除一个块"""
        tf = self._docs.pop(cid, None)
        if tf is None:
            return
        self._doc_len.pop(cid, None)
        for term in tf:
            self._df[term] -= 1
            if self._df[term] <= 0:
                del self._df[term]

    def build(self, items: list[tuple[str, str]]) -> None:
        """全量重建：items = [(chunk_id, text), ...]"""
        self._docs.clear()
        self._doc_len.clear()
        self._df.clear()
        for cid, text in items:
            tokens = tokenize(text)
            if tokens:
                self._add_doc(cid, tokens)

    # ------------------------------------------------------------------
    def search(self, query: str, top_n: int = 10) -> list[tuple[str, float]]:
        """BM25 检索，返回 [(chunk_id, score), ...] 按得分降序"""
        if not self._docs:
            return []
        q_tokens = tokenize(query)
        scores: dict[str, float] = {}
        n_docs = len(self._docs)
        avg_len = self._avg_len or 1.0

        for cid, tf in self._docs.items():
            dl = self._doc_len[cid]
            score = 0.0
            for qt in q_tokens:
                f = tf.get(qt, 0)
                if f == 0:
                    continue
                df = self._df.get(qt, 0)
                # IDF：df 越小权重越高
                idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
                denom = f + self.k1 * (1 - self.b + self.b * dl / avg_len)
                score += idf * f * (self.k1 + 1) / denom
            if score > 0:
                scores[cid] = score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_n]
