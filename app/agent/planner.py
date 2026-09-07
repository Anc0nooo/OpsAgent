"""
Agent 规划器 - 核心编排（状态机流转，prompt v2，多用户架构）
流程：
1. 新问题（IDLE/DONE）：
   意图二分（chat 闲聊 / work 工作）→ 闲聊直答；
   工作类一律先 RAG 检索（知识 + 表结构）→ 单次结构化调用输出 JSON
   （need_query/sql/sql_purpose/answer）→ 只读 SQL 挂起或直接收敛
2. 挂起续推（QUERY_PENDING）：
   人工回传 SQL 结果 → 注入上下文 → 继续分析（未达上限可继续挂起，达上限强制收敛）
3. SQL 出口统一过 ReadOnlySQLGuard 强校验，不过则反馈 LLM 重试一次。

多用户：
- handle_message / handle_message_stream 接收 db + user_id
- LLM 调用用 get_user_llm(db, user_id)（用户自己的 API Key/模型配置）
- 会话存储/RAG 检索均按 user_id 隔离
"""
import asyncio
import json
import logging

from typing import AsyncIterator, Any

from sqlalchemy.orm import Session

from app.agent import prompts, store
from app.agent.context import build_history, build_rag_context, build_rag_context_detailed
from app.agent.state import AgentState
from app.config.settings import settings
from app.core.llm import LLMApiError, get_user_llm
from app.core.sql_guard import sql_guard
from app.db.models import User
from app.output.formatter import attach_sources, build_summary_sections

logger = logging.getLogger(__name__)

# 挂起回复模板渲染
_PENDING_TMPL = prompts.PENDING_NOTICE


def _hit_level(rag_context: str) -> int:
    """从 rag_context 的统计头提取命中层级（0=无命中，1=仅表结构，2=仅知识，3=充分命中）"""
    import re
    m = re.search(r"知识\s*(\d+)\s*条\s*\+\s*表结构\s*(\d+)\s*条", rag_context)
    if not m:
        return 3  # 无法解析时假设充分命中，放宽限制
    g, s = int(m.group(1)), int(m.group(2))
    if g == 0 and s == 0:
        return 0
    if g == 0 and s > 0:
        return 1
    if g > 0 and s == 0:
        return 2
    return 3


def _codeblock_forbid_hint(level: int) -> str:
    """根据命中层级返回动态追加的代码块禁止提示（追加到 stream/plan prompt 末尾）"""
    if level == 0:
        return (
            "\n\n⚠️⚠️⚠️ FINAL HARD CONSTRAINT (VIOLATION = FAILURE): "
            "Knowledge base has 0 hits. Your plan body MUST NOT contain ANY code block of any kind — "
            "no ```sql, no ```sqlplus, no ```shell, no ```bash, no ```plsql, no bare ``` fences, "
            "no ALTER SYSTEM/ALTER TABLE/CREATE/DROP/INSERT/UPDATE/DELETE command blocks. "
            "Describe ALL operations in plain text only (e.g., write 'increase undo_retention parameter', "
            "NOT write an ALTER SYSTEM SET command block)."
        )
    elif level == 1:
        return (
            "\n\n补充：本次仅命中表结构，方案中 SQL 代码块仍需知识库有对应示例才能输出，"
            "不要凭空编造 ALTER SYSTEM 等管理命令。"
        )
    return ""


def _slice_text(text: str, size: int = 80) -> list[str]:
    """把长文本切成小块，模拟流式打字机增量（LLM 已一次生成 plan，前端仍逐块呈现）"""
    if not text:
        return []
    return [text[i:i + size] for i in range(0, len(text), size)]


def _friendly_error(error_code: str, raw: str) -> str:
    """把后端 error_code 翻译成用户能看懂的中文提示"""
    tips = {
        "invalid_key": "API Key 无效或未配置。请点击右上角⚙设置，填入百炼 API-KEY 后重试。",
        "quota_exceeded": "API 额度不足或已限流。请前往百炼控制台充值/查看用量，或稍后重试。",
        "forbidden": "API Key 无权限访问该模型。请在百炼控制台确认已开通 qwen-plus、text-embedding-v3、gte-rerank 等模型。",
        "model_not_found": f"指定的模型不存在：{raw}",
        "network": "网络连接异常。请检查网络是否通畅，或稍后重试。",
    }
    return tips.get(error_code, f"服务调用失败（{error_code}）：{raw}")


def _plan_answer(result: dict) -> str:
    """从结构化结果取正文（v2 字段 answer；兼容旧字段 plan）"""
    return str(result.get("answer") or result.get("plan") or "").strip()


def _evaluation(result: dict) -> dict | None:
    """
    从结构化结果提取回答质量自评（LLM 自报）。
    三项全空/无法识别时返回 None（前端不渲染评估卡片，如闲聊直答）。
    """
    def norm_level(v: Any) -> str:
        s = str(v or "").strip()
        return s if s in ("高", "中", "低") else ""

    cov = norm_level(result.get("source_coverage"))
    risk = norm_level(result.get("hallucination_risk"))
    acc = str(result.get("accuracy") or "").strip()
    if not (cov or risk or acc):
        return None
    return {"source_coverage": cov, "accuracy": acc, "hallucination_risk": risk}


class Planner:
    """规划器（无状态编排，会话状态存 MySQL，按 user_id 隔离）"""

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------
    def handle_message(self, db: Session, user: User, session_id: str | None, text: str) -> dict:
        """
        处理一条用户消息，返回助手回复。
        返回: {"session_id", "content", "state", "query_round", "pending_query"?, "need_query"}
        """
        user_id = user.id
        # 会话不存在则创建（绑定当前用户，标题取问题前 20 字）
        session = store.get_session(db, session_id, user_id) if session_id else None
        if session is None:
            session = store.create_session(db, user_id, title=text[:20])
        sid = session["id"]

        # 先构建"不含当前消息"的历史快照（供 prompt 注入），再落库当前消息
        history_text = build_history(db, sid)
        store.add_message(db, sid, "user", text)
        state = AgentState(session["state"])

        # 状态分流
        if state == AgentState.QUERY_PENDING:
            reply = self._continue_analysis(db, user, sid, session, text, history_text)
        elif state == AgentState.DONE:
            # DONE 后再来消息：视为新问题（继续追问走 PLANNING）
            reply = self._new_question(db, user, sid, text, history_text)
        else:
            reply = self._new_question(db, user, sid, text, history_text)

        store.add_message(db, sid, "assistant", reply["content"])
        db.commit()
        return reply

    # ------------------------------------------------------------------
    # 新问题主流程
    # ------------------------------------------------------------------
    def _new_question(self, db: Session, user: User, sid: str, text: str, history_text: str) -> dict:
        user_id = user.id
        llm = get_user_llm(db, user_id)
        store.update_session(db, sid, user_id, state=AgentState.PLANNING.value)

        # 1. 意图识别（chat 闲聊直答；work 一律走 RAG + 单次结构化调用）
        intent = self._detect_intent(db, user_id, text)
        if intent == "chat":
            content = llm.chat([
                {"role": "system", "content": prompts.SYSTEM_PROMPT},
                {"role": "user", "content": prompts.CHAT_PROMPT.format(text=text)},
            ])
            store.update_session(db, sid, user_id, state=AgentState.DONE.value, clear_pending=True)
            return self._pack(sid, content, AgentState.DONE, need_query=False)

        # 2. RAG 检索（普通知识 + 表结构；工作类问题一律先检索，含问人/问文档/问事实）
        rag_context = build_rag_context(db, user_id, text)

        # 3. 单次结构化调用：need_query 判断 + 正文生成
        forbid_hint = _codeblock_forbid_hint(_hit_level(rag_context))
        prompt = prompts.PLAN_PROMPT.format(
            rag_context=rag_context,
            history=history_text,
            text=text,
            status_note="这是新问题。知识库有内容则直接基于知识库回答；仅在必须依赖真实环境数据时才挂起查询。" + forbid_hint,
        )
        result = self._plan_llm(db, user_id, prompt)

        # 4. 按需挂起或直接收敛
        return self._resolve(db, user, sid, result, query_round=0)

    # ------------------------------------------------------------------
    # 挂起续推（人工回传后）
    # ------------------------------------------------------------------
    def _continue_analysis(self, db: Session, user: User, sid: str, session: dict,
                           text: str, history_text: str) -> dict:
        user_id = user.id
        llm = get_user_llm(db, user_id)
        pending = json.loads(session["pending_query"]) if session["pending_query"] else {}
        query_round = session["query_round"] or 0
        purpose = pending.get("purpose", "（未知）")

        store.update_session(db, sid, user_id, state=AgentState.PLANNING.value)

        # 轮次上限：已达上限 → 强制收敛（prompt 中注明，并忽略 LLM 的 need_query=true）
        reached_limit = query_round >= settings.QUERY_MAX_ROUNDS

        # 回传结果包装（标注脱敏提醒，隐私红线）
        feedback = f"【第 {query_round} 轮 SQL 执行结果回传（查询目的：{purpose}；用户可能已脱敏）】\n{text}"

        rag_context = build_rag_context(db, user_id, feedback[:500])  # 用回传内容做一次补充检索
        forbid_hint = _codeblock_forbid_hint(_hit_level(rag_context))
        prompt = prompts.PLAN_PROMPT.format(
            rag_context=rag_context,
            history=history_text,
            text=feedback,
            status_note=prompts.CONTINUE_NOTE.format(round=query_round, max_rounds=settings.QUERY_MAX_ROUNDS) + forbid_hint,
        )
        result = self._plan_llm(db, user_id, prompt)

        if reached_limit:
            result["need_query"] = False  # 硬上限，强制收敛
            result["sql"], result["sql_purpose"] = "", ""
            if not _plan_answer(result):
                result["answer"] = "已达查询轮次上限，请基于以上信息人工判断；或新开会话补充问题描述。"

        return self._resolve(db, user, sid, result, query_round=query_round)

    # ------------------------------------------------------------------
    # 结果处理：挂起 or 收敛
    # ------------------------------------------------------------------
    def _resolve(self, db: Session, user: User, sid: str, result: dict, query_round: int) -> dict:
        """根据 LLM 结构化结果决定挂起（SQL 校验）或输出方案"""
        user_id = user.id
        llm = get_user_llm(db, user_id)
        need_query = bool(result.get("need_query")) and result.get("sql")

        if need_query:
            sql = str(result["sql"]).strip()
            # 只读校验（硬约束）：不过则反馈重试一次
            guard = sql_guard.validate(sql)
            if not guard.ok:
                logger.warning("SQL 只读校验未通过：%s，重试一次", guard.reason)
                retry_prompt = (
                    "你上一条 SQL 未通过只读校验，原因：" + guard.reason
                    + "\n原 SQL：" + sql
                    + "\n请重新输出 JSON（格式不变），SQL 必须是单条 Oracle 只读 SELECT/WITH 查询。"
                )
                result = self._plan_llm(db, user_id, retry_prompt)
                need_query = bool(result.get("need_query")) and result.get("sql")
                if need_query:
                    sql = str(result["sql"]).strip()
                    guard = sql_guard.validate(sql)
                    if not guard.ok:
                        # 二次仍失败：放弃挂起，直接输出当前分析
                        logger.error("SQL 二次校验仍失败，降级输出方案")
                        need_query = False
                        result["answer"] = (
                            _plan_answer(result) or "（查询 SQL 未能通过只读校验）"
                        ) + f"\n\n> 注：自动生成的 SQL 未通过只读校验（{guard.reason}），请人工编写查询。"

        if need_query:
            # 挂起：保存 pending_query，输出 SQL + 目的 + 隐私提示
            new_round = query_round + 1
            pending = {
                "sql": sql,
                "purpose": result.get("sql_purpose", ""),
                "round": new_round,
            }
            store.update_session(
                db, sid, user_id, state=AgentState.QUERY_PENDING.value,
                pending_query=pending, query_round=new_round,
            )
            content = (_plan_answer(result) or "需要查询真实环境数据来继续排查。") + _PENDING_TMPL.format(
                sql=sql, purpose=pending["purpose"]
            )
            return self._pack(sid, content, AgentState.QUERY_PENDING, need_query=True,
                              pending=pending, evaluation=_evaluation(result))

        # 收敛：输出方案
        store.update_session(
            db, sid, user_id, state=AgentState.DONE.value, clear_pending=True,
            query_round=query_round,
        )
        content = _plan_answer(result) or "（未能生成有效方案，请换个说法再试）"
        return self._pack(sid, content, AgentState.DONE, need_query=False, evaluation=_evaluation(result))

    # ==================================================================
    # 流式编排（SSE）：yield 事件 {"type": status|delta|done, ...}
    # ==================================================================
    async def handle_message_stream(self, db: Session, user: User,
                                    session_id: str | None, text: str) -> AsyncIterator[dict[str, Any]]:
        """流式处理一条用户消息，逐事件产出（SSE 消费）"""
        # 外层 try/except：捕获所有异常（含 LLMApiError），emit 结构化 error 事件
        try:
            async for ev in self._handle_stream_inner(db, user, session_id, text):
                yield ev
        except LLMApiError as e:
            # API 调用类错误：按 error_code 给前端友好提示
            yield {
                "type": "error",
                "error_code": e.error_code,
                "message": _friendly_error(e.error_code, str(e)),
                "detail": str(e),
            }
        except Exception as e:  # noqa: BLE001 兜底
            logger.exception("handle_message_stream 未捕获异常")
            yield {
                "type": "error",
                "error_code": "unknown",
                "message": f"服务异常：{e}",
                "detail": str(e),
            }

    async def _handle_stream_inner(self, db: Session, user: User,
                                   session_id: str | None, text: str) -> AsyncIterator[dict[str, Any]]:
        """流式内部主流程（不含外层错误兜底）"""
        user_id = user.id
        llm = get_user_llm(db, user_id)
        session = store.get_session(db, session_id, user_id) if session_id else None
        if session is None:
            session = store.create_session(db, user_id, title=text[:20])
        sid = session["id"]

        # 历史快照（不含当前消息）→ 落库当前消息
        history_text = build_history(db, sid)
        store.add_message(db, sid, "user", text)
        state = AgentState(session["state"])

        yield {"type": "status", "stage": "intent", "text": "识别问题意图…"}

        # 挂起态：回传续推；否则按新问题处理
        if state == AgentState.QUERY_PENDING:
            pending = json.loads(session["pending_query"]) if session["pending_query"] else {}
            query_round = session["query_round"] or 0
            purpose = pending.get("purpose", "（未知）")
            feedback = f"【第 {query_round} 轮 SQL 执行结果回传（查询目的：{purpose}；用户可能已脱敏）】\n{text}"
            status_note = prompts.CONTINUE_NOTE.format(round=query_round, max_rounds=settings.QUERY_MAX_ROUNDS)
            reached_limit = query_round >= settings.QUERY_MAX_ROUNDS
            judge_text, rag_query = feedback, feedback[:500]
        else:
            # 意图识别是同步 LLM 调用，必须放线程池执行，避免阻塞事件循环
            intent = await asyncio.to_thread(self._detect_intent, db, user_id, text)
            if intent == "chat":
                # 闲聊直答：流式打字机（避免长时间无反馈）
                yield {"type": "status", "stage": "reply", "text": "生成回复…"}
                chat_prompt = prompts.CHAT_PROMPT.format(text=text)
                content_parts: list[str] = []
                async for delta in llm.chat_stream_async(
                    [{"role": "system", "content": prompts.SYSTEM_PROMPT}, {"role": "user", "content": chat_prompt}]
                ):
                    content_parts.append(delta)
                    yield {"type": "delta", "content": delta}
                content = "".join(content_parts) or "（回复生成失败，请换个说法再试）"
                store.add_message(db, sid, "assistant", content)
                store.update_session(db, sid, user_id, state=AgentState.DONE.value, clear_pending=True)
                db.commit()
                yield {"type": "done", "session_id": sid, "state": AgentState.DONE.value,
                       "need_query": False, "query_round": 0}
                return
            status_note = "这是新问题。知识库有内容则直接基于知识库回答；仅在必须依赖真实环境数据时才挂起查询。"
            reached_limit = False
            query_round = 0  # 新问题从第 0 轮开始（挂起时 +1）
            judge_text, rag_query = text, text

        # RAG 检索（同步检索 + 重排，放线程池；流式版带来源记录）
        yield {"type": "status", "stage": "rag", "text": "检索知识库…"}
        rag_context, rag_sources = await asyncio.to_thread(build_rag_context_detailed, db, user_id, rag_query)

        # ① 单次 structured 调用：判断 need_query + 生成正文（prompt v2，省 token）
        yield {"type": "status", "stage": "analyze", "text": "分析问题…"}
        forbid_hint = _codeblock_forbid_hint(_hit_level(rag_context))
        plan_prompt = prompts.PLAN_PROMPT.format(
            rag_context=rag_context, history=history_text, text=judge_text,
            status_note=status_note + forbid_hint,
        )
        result = await asyncio.to_thread(
            llm.structured_output,
            [{"role": "system", "content": prompts.SYSTEM_PROMPT}, {"role": "user", "content": plan_prompt}],
        )
        need_query = bool(result.get("need_query")) and result.get("sql") and not reached_limit

        # ② SQL 只读校验（need_query=true 时），失败反馈重试一次
        if need_query:
            sql = str(result["sql"]).strip()
            guard = sql_guard.validate(sql)
            if not guard.ok:
                logger.warning("流式 SQL 校验未通过：%s，重试一次", guard.reason)
                result = await asyncio.to_thread(llm.structured_output, [
                    {"role": "system", "content": prompts.SYSTEM_PROMPT},
                    {"role": "user", "content": plan_prompt},
                    {"role": "user", "content": "上一条 SQL 未通过只读校验（原因：" + guard.reason
                        + "）：\n" + sql + "\n请重新输出 JSON，SQL 必须是单条 Oracle 只读 SELECT/WITH 查询。"},
                ])
                need_query = bool(result.get("need_query")) and result.get("sql")
                if need_query:
                    sql = str(result["sql"]).strip()
                    guard = sql_guard.validate(sql)
                    if not guard.ok:
                        need_query = False
                        result["answer"] = _plan_answer(result) + f"\n\n> 注：自动生成的 SQL 未通过只读校验（{guard.reason}），请人工编写查询。"

        # 回答质量自评（LLM 结构化输出自报；闲聊直答路径不产出该字段，前端自动不渲染卡片）
        evaluation = _evaluation(result)

        # ③ 挂起：answer + SQL 卡片 + 隐私提示
        if need_query:
            new_round = query_round + 1
            pending = {"sql": sql, "purpose": result.get("sql_purpose", ""), "round": new_round}
            content = (_plan_answer(result) or "需要查询真实环境数据来继续排查。") + _PENDING_TMPL.format(
                sql=sql, purpose=pending["purpose"],
            )
            store.update_session(db, sid, user_id, state=AgentState.QUERY_PENDING.value,
                                 pending_query=pending, query_round=new_round)
            yield {"type": "delta", "content": content}
            store.add_message(db, sid, "assistant", content)
            db.commit()
            yield {"type": "done", "session_id": sid, "state": AgentState.QUERY_PENDING.value,
                   "need_query": True, "query_round": new_round, "pending_query": pending,
                   "sources": rag_sources, "evaluation": evaluation}
            return

        # ④ 正文：切片模拟流式打字机（单次 structured 已生成 answer，前端逐块呈现）
        yield {"type": "status", "stage": "plan", "text": "生成回复…"}
        content = _plan_answer(result) or "（未能生成有效回复，请换个说法再试）"
        if reached_limit:
            content += "\n\n（已达查询轮次上限，以上基于现有信息收敛输出最终方案）"
        for chunk in _slice_text(content):
            yield {"type": "delta", "content": chunk}
        # 结果输出：方案末尾附加引用来源 + 四段总结结构（供前端卡片与导出）
        content = attach_sources(content, rag_sources)
        sections = build_summary_sections(content)
        store.add_message(db, sid, "assistant", content)
        store.update_session(db, sid, user_id, state=AgentState.DONE.value, clear_pending=True,
                             query_round=session["query_round"] or 0)
        db.commit()
        yield {"type": "done", "session_id": sid, "state": AgentState.DONE.value,
               "need_query": False, "query_round": session["query_round"] or 0,
               "sections": sections, "sources": rag_sources, "evaluation": evaluation}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _detect_intent(self, db: Session, user_id: int, text: str) -> str:
        """意图识别（chat 闲聊 / work 工作；失败默认 work，宁可多走检索不误判闲聊）"""
        try:
            llm = get_user_llm(db, user_id)
            out = llm.structured_output([
                {"role": "system", "content": prompts.SYSTEM_PROMPT},
                {"role": "user", "content": prompts.INTENT_PROMPT.format(text=text[:500])},
            ])
            intent = str(out.get("intent", "work")).strip().lower()
            return "chat" if intent == "chat" else "work"
        except Exception as e:  # noqa: BLE001
            logger.warning("意图识别失败，默认按工作问题处理: %s", e)
            return "work"

    def _plan_llm(self, db: Session, user_id: int, prompt: str) -> dict:
        """结构化方案生成（SYSTEM_PROMPT 统一注入，人设+边界全局生效）"""
        llm = get_user_llm(db, user_id)
        messages = [{"role": "system", "content": prompts.SYSTEM_PROMPT}]
        messages.append({"role": "user", "content": prompt})
        out = llm.structured_output(messages)
        # 容错：LLM 可能输出字符串包裹
        if isinstance(out, str):
            out = json.loads(out)
        return out if isinstance(out, dict) else {}

    @staticmethod
    def _pack(sid: str, content: str, state: AgentState, need_query: bool,
              pending: dict | None = None, evaluation: dict | None = None) -> dict:
        """统一回复结构"""
        reply = {
            "session_id": sid,
            "content": content,
            "state": state.value,
            "need_query": need_query,
        }
        if pending:
            reply["pending_query"] = pending
        if evaluation:
            reply["evaluation"] = evaluation
        return reply


# 全局单例
planner = Planner()
