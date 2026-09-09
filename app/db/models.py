"""
全部 ORM 模型（多用户架构 + 管理员角色 + 操作日志）

表：
- users：用户（用户名 + bcrypt 哈希密码 + 角色 role: ancon/user）
- user_configs：每用户的模型配置（API Key / base_url / 模型名）
- knowledge_docs：知识文档（加 user_id 隔离）
- knowledge_chunks：知识块（通过 doc_id 间接隔离）
- conversations：会话（加 user_id 隔离）
- messages：消息（通过 conversation_id 间接隔离）
- operation_logs：操作日志（管理员可见，按 user_id / 时间索引）
"""
from sqlalchemy import (
    BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from app.db.engine import Base


class User(Base):
    """用户表（role: ancon=管理员 / user=普通用户）"""
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    avatar = Column(MEDIUMTEXT, nullable=True)  # 头像 data URL（base64）；NULL/空 = 前端用用户名首字符兜底
    role = Column(String(20), nullable=False, default="user")  # ancon / user
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))
    status = Column(Integer, nullable=False, default=1)  # 1=正常 0=禁用


class UserConfig(Base):
    """每用户的模型配置（替代全局 .env）"""
    __tablename__ = "user_configs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(50), nullable=False, default="dashscope")
    api_key = Column(String(255), nullable=False, default="")
    base_url = Column(String(255), nullable=False,
                      default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    chat_model = Column(String(100), nullable=False, default="qwen-plus")
    embed_model = Column(String(100), nullable=False, default="text-embedding-v3")
    rerank_model = Column(String(100), nullable=False, default="gte-rerank-v2")
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False,
                        server_default=sa_text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("uniq_user_provider", "user_id", "provider", unique=True),
    )


class KnowledgeDoc(Base):
    """知识文档（按 user_id 隔离）"""
    __tablename__ = "knowledge_docs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    doc_type = Column(String(20), nullable=False, default="other")  # guide/bug/schema/other
    source = Column(String(255), nullable=False, default="")
    chunk_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False,
                        server_default=sa_text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_doc_user", "user_id"),
    )


class KnowledgeChunk(Base):
    """知识块（通过 doc_id 间接关联用户）"""
    __tablename__ = "knowledge_chunks"

    id = Column(String(128), primary_key=True)  # 格式：{doc_id}_{seq}
    doc_id = Column(BigInteger, ForeignKey("knowledge_docs.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Integer, nullable=False)
    section = Column(String(255), nullable=False, default="")
    text = Column(Text, nullable=False)
    doc_type = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_chunk_doc", "doc_id"),
    )


class Conversation(Base):
    """会话（按 user_id 隔离）"""
    __tablename__ = "conversations"

    id = Column(String(32), primary_key=True)  # uuid hex
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False, default="新对话")
    state = Column(String(20), nullable=False, default="IDLE")  # IDLE/PLANNING/QUERY_PENDING/DONE
    pending_query = Column(Text, nullable=True)  # JSON: {sql, purpose, round}
    query_round = Column(Integer, nullable=False, default=0)
    is_pinned = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False,
                        server_default=sa_text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_conv_user", "user_id"),
    )


class Message(Base):
    """消息（通过 conversation_id 间接关联用户）"""
    __tablename__ = "messages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id = Column(String(32), ForeignKey("conversations.id", ondelete="CASCADE"),
                              nullable=False)
    role = Column(String(20), nullable=False)  # user/assistant/system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_msg_conv", "conversation_id", "id"),
    )


class OperationLog(Base):
    """操作日志（管理员可见；记录登录/对话/上传/删除/检索/配置等关键操作）"""
    __tablename__ = "operation_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    username = Column(String(50), nullable=True)  # 冗余存用户名，删用户后仍可查
    action = Column(String(50), nullable=False)   # login/logout/chat/upload_doc/delete_doc/search/retrieve/config_save/sql_query/register 等
    detail = Column(Text, nullable=True)          # 操作详情（摘要）
    ip = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_log_user", "user_id"),
        Index("idx_log_time", "created_at"),
    )


class AppVersion(Base):
    """应用版本与更新日志（单行表，id 恒为 1；管理员在后台维护）

    - version：当前版本号，如 v1.1
    - changelog：更新日志，Markdown 富文本（前端 marked 渲染）
    前端登录后比对 localStorage 的 last_version，不一致则弹出更新弹窗。
    """
    __tablename__ = "app_version"

    id = Column(Integer, primary_key=True, autoincrement=False)  # 恒为 1（单行）
    version = Column(String(20), nullable=False, default="v1.0")
    changelog = Column(MEDIUMTEXT, nullable=False, default="")
    updated_at = Column(DateTime, nullable=False,
                        server_default=sa_text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))
