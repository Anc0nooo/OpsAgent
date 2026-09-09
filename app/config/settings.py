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
    CHUNK_MAX_CHARS: int = 600               # 切分块最大字符数（中文按字符；语义边界递归切）
    CHUNK_OVERLAP_CHARS: int = 150           # 相邻块重叠字符数（足够大，保证跨块句子完整）
    CHUNK_WHOLE_MAX_CHARS: int = 1000        # 短文档阈值：正文小于此长度整块入库，不切分
    RAG_NEIGHBOR_WINDOW: int = 1             # 检索后同文档相邻块合并窗口（命中块 seq±N 合并为一个片段）
    RETRIEVAL_TOP_N: int = 10                # 混合召回候选数（送重排前）
    BM25_WEIGHT: float = 0.5                 # BM25 融合权重
    VECTOR_WEIGHT: float = 0.5               # 向量融合权重
    EMBED_BATCH_SIZE: int = 10               # 向量化单批上限（百炼限制）

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
