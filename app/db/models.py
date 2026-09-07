"""
全部 ORM 模型（多用户架构）

表：
- users：用户（用户名 + bcrypt 哈希密码）
- user_configs：每用户的模型配置（API Key / base_url / 模型名）
- knowledge_docs：知识文档（加 user_id 隔离）
- knowledge_chunks：知识块（通过 doc_id 间接隔离）
- conversations：会话（加 user_id 隔离）
- messages：消息（通过 conversation_id 间接隔离）
"""
from sqlalchemy import (
    BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from app.db.engine import Base


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    avatar = Column(MEDIUMTEXT, nullable=True)  # 头像 data URL（base64）；NULL/空 = 前端用用户名首字符兜底
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
