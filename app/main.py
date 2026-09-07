"""
OpsAgent 运维智能体 - 后端入口（多用户架构）
四模块路由 + 认证路由：
- rag/（知识库）  agent/（规划器）  output/（输出）  auth/（认证）
"""
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent import store as agent_store
from app.agent.api import router as agent_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.settings import router as settings_router
from app.auth.api import router as auth_router
from app.config.settings import settings
from app.db.engine import init_db
from app.output.api import router as output_router
from app.rag.api import router as knowledge_router
from app.rag.service import knowledge_service

# 统一日志格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="日常运维 AI 智能体（RAG + 规划器 + 工具调用 + 结果输出，Oracle 方言，只读人工在环，多用户）",
)

# CORS：允许本地前端 dev 服务器跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health_router, tags=["健康检查"])
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(knowledge_router)
app.include_router(agent_router)
app.include_router(chat_router)
app.include_router(output_router)


# 全局异常兜底：统一返回 {code, message, data}
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logging.getLogger(__name__).exception("未捕获异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": f"服务器内部错误: {exc}", "data": None},
    )


@app.on_event("startup")
async def on_startup() -> None:
    """启动初始化：MySQL 建表 + Chroma + BM25"""
    # MySQL 建表（users / user_configs / knowledge_docs / ...）
    try:
        init_db()
        logging.getLogger(__name__).info("MySQL 表初始化完成")
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).exception("MySQL 初始化失败: %s", e)

    # 检查 API-KEY 是否已配置（不阻断启动）
    if not settings.DASHSCOPE_API_KEY:
        logging.getLogger(__name__).warning(
            "未配置 DASHSCOPE_API_KEY（用户需在设置弹窗中配置自己的 Key）"
        )
    # 初始化 RAG 知识库（Chroma + BM25）
    try:
        knowledge_service.initialize()
        agent_store.init_store()
        logging.getLogger(__name__).info("RAG 知识库与 Agent 会话存储初始化完成")
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).exception("RAG/Agent 初始化失败: %s", e)
