"""
SQLAlchemy 引擎与会话工厂
- MySQL 8.0（mysql+pymysql），charset=utf8mb4
- 启动时 create_all 自动建表
- get_db 依赖注入：每个请求一个独立 session，自动关闭
"""
import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config.settings import settings

logger = logging.getLogger(__name__)


def _build_url() -> str:
    """构造 MySQL 连接 URL"""
    return (
        f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}"
        f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}"
        f"?charset=utf8mb4"
    )


engine = create_engine(
    _build_url(),
    pool_pre_ping=True,   # 连接池预检（MySQL wait_timeout 自动重连）
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


def get_db():
    """FastAPI 依赖：每个请求一个独立 DB session，请求结束自动关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_schema() -> None:
    """轻量迁移：create_all 不会给已存在的表补列，这里按需 ALTER TABLE"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "users" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("users")}
        if "avatar" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN avatar MEDIUMTEXT NULL"))
            logger.info("users 表已补充 avatar 列")


def init_db() -> None:
    """建表（幂等：已存在的表不会被重建）+ 轻量补列迁移"""
    # 导入所有模型，确保 create_all 能发现它们
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_schema()
    logger.info("MySQL 表已就绪: %s@%s:%s/%s", settings.MYSQL_USER, settings.MYSQL_HOST,
                settings.MYSQL_PORT, settings.MYSQL_DATABASE)
