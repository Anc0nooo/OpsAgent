"""
全局配置模块
从项目根目录 .env 文件读取配置，提供统一的配置访问入口。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（本文件位于 app/config/ 下，向上两级）
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """应用配置：所有字段均可通过 .env 覆盖"""

    # ---- 应用基础 ----
    APP_NAME: str = "OpsAgent 运维智能体"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # ---- 服务 ----
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # ---- MySQL 8.0 ----
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "opsagent"

    # ---- JWT 鉴权 ----
    JWT_SECRET: str = "OpsAgent2026SecretKeyForJwtTokenGenerationAtLeast32Chars"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 5  # token 有效期（小时）：登录一次后 5 小时需重新登录
    JWT_EXPIRE_MINUTES: int = 0  # 调试用：>0 时按分钟过期（便于自测），正式环境留 0

    # ---- 管理员 ----
    ADMIN_USERNAME: str = ""  # 启动时将该用户自动设为 ancon；留空则首个注册用户为 ancon

    # ---- 阿里百炼（全局默认；用户未单独配置时兜底）----
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    CHAT_MODEL: str = "qwen-plus"            # 对话默认模型（阿里百炼）
    BACKUP_CHAT_MODEL: str = "qwen-flash"    # 失败自动降级模型（留空 = 不降级）
    REASONING_MODEL: str = ""                # 复杂 bug 推理模型（留空 = 用 CHAT_MODEL）
    EMBED_MODEL: str = "text-embedding-v3"   # 向量模型（百炼）
    RERANK_MODEL: str = "gte-rerank-v2"      # 重排模型（gte-rerank 旧版已停用）
    # 重排走 DashScope 原生接口（OpenAI 兼容模式不支持 rerank）
    RERANK_API_URL: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

    # ---- LLM 调用参数 ----
    LLM_MAX_RETRIES: int = 2       # 失败重试次数
    LLM_TIMEOUT: float = 60.0      # 单次请求超时（秒）
    LLM_TEMPERATURE: float = 0.3   # 默认温度（运维场景求稳，偏低）

    # ---- SQL 方言（硬约束：锁定 Oracle）----
    SQL_DIALECT: str = "oracle"

    # ---- 本地存储 ----
    DATA_DIR: Path = BASE_DIR / "data"       # 数据目录（SQLite + Chroma）
    # ---- RAG 分块策略（按文档类型 doc_type 差异化）----
    # 每类三个参数：
    #   chunk_size:      单块目标最大字符数（正文语义切分上限 / 表格按行分批的批大小）
    #   chunk_overlap:   相邻正文块的重叠字符数（表格块不做字符重叠，改为重复表头保证自洽）
    #   whole_threshold: 整段（正文/一张表）短于此长度则整块入库不切分；
    #                    表结构类调大（4000），优先"一张表一个块"，避免字段与注释被切断
    # 如需整体调参，可在 .env 以 JSON 覆盖 CHUNK_PROFILES。
    CHUNK_PROFILES: dict[str, dict[str, int]] = {
        "schema": {"chunk_size": 1000, "chunk_overlap": 50, "whole_threshold": 4000},  # 表结构类
        "guide":  {"chunk_size": 700,  "chunk_overlap": 200, "whole_threshold": 1000},  # 操作指导类
        "bug":    {"chunk_size": 600,  "chunk_overlap": 100, "whole_threshold": 800},   # BUG修复类
        "other":  {"chunk_size": 500,  "chunk_overlap": 100, "whole_threshold": 700},   # 其他
    }
    CHUNK_DEFAULT_TYPE: str = "other"       # 未知 doc_type 时回退的分块档

    # ---- 每用户可调切分参数（模式B：guide/bug/other）的取值范围与默认值 ----
    KB_CHUNK_SIZE_DEFAULT: int = 500
    KB_CHUNK_SIZE_MIN: int = 300
    KB_CHUNK_SIZE_MAX: int = 1000
    KB_OVERLAP_DEFAULT: int = 50
    KB_OVERLAP_MIN: int = 0
    KB_OVERLAP_MAX: int = 200
    # 模式A（语义切分，不用 chunk_size）：医疗文件长段落二次切分上限
    MEDICAL_LONG_PARAGRAPH: int = 2000

    # ---- 图片 / OCR（PDF/Word 内嵌图片；qwen-vl 视觉模型，避免 2核2G 跑本地 OCR）----
    OCR_ENABLED: bool = True                # False 时仅提取原图与位置，不调视觉模型
    VISION_MODEL: str = "qwen-vl-plus"      # OCR 视觉模型（用用户配置的同一 API Key）
    OCR_MAX_IMAGES_PER_DOC: int = 20        # 单篇文档 OCR 图片数上限（控成本/控时长）
    OCR_MIN_IMAGE_PX: int = 64              # 过小图片（图标）跳过 OCR
    SCANNED_PAGE_MIN_CHARS: int = 30        # 页文本少于该值视为扫描件，整页渲染走 OCR

    # ---- 图片与解析缓存目录（DATA_DIR 下）----
    # data/images/user_{id}/doc_{id}/xxx.png；data/parsed_cache/{doc_id}.json
    @property
    def IMAGES_DIR(self):  # noqa: N802 - 保持配置项风格
        return self.DATA_DIR / "images"

    @property
    def PARSED_CACHE_DIR(self):  # noqa: N802
        return self.DATA_DIR / "parsed_cache"
    RAG_NEIGHBOR_WINDOW: int = 1             # 检索后同文档相邻块合并窗口（命中块 seq±N 合并为一个片段）
    RETRIEVAL_TOP_N: int = 10                # 混合召回候选数（送重排前）
    BM25_WEIGHT: float = 0.5                 # BM25 融合权重
    VECTOR_WEIGHT: float = 0.5               # 向量融合权重
    EMBED_BATCH_SIZE: int = 16               # 向量化单批上限（百炼 text-embedding-v3 单批 25 条，16 留余量）

    # ---- 会话与查询约束 ----
    # 上下文保留最近"轮数"（1 轮 = 1 user + 1 assistant = 2 条消息）；
    # build_history / get_history 内部会按 turns*2 取条数，保证完整轮次
    HISTORY_MAX_TURNS: int = 8
    QUERY_MAX_ROUNDS: int = 5      # 人工在环查询轮次上限
    PASTE_MAX_LINES: int = 30      # 回传结果粘贴行数上限

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


# 全局单例配置
settings = Settings()
