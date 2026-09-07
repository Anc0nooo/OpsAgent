"""
Agent 规划器 - 状态机定义
状态流转：
    IDLE ──用户提问──> PLANNING ──知识足够──> DONE（输出完整方案）
                          │
                          └──需要真实数据──> QUERY_PENDING（挂起，产出只读 SQL）
                                                 │ 人工执行→脱敏回传
                                                 ├──继续推理(未超5轮)──> QUERY_PENDING 或 DONE
                                                 └──达到5轮上限──> 强制收敛 DONE
"""
from enum import Enum


class AgentState(str, Enum):
    """会话状态"""
    IDLE = "IDLE"                    # 空闲（新会话/已完成，等待新问题）
    PLANNING = "PLANNING"            # 推理中（过渡态，处理完即转出）
    QUERY_PENDING = "QUERY_PENDING"  # 挂起：等待人工执行只读 SQL 并回传结果
    DONE = "DONE"                    # 本问题排查完成，已输出方案


# 合法流转表（当前状态 -> 允许到达的状态集合）
TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE: {AgentState.PLANNING, AgentState.DONE},
    AgentState.PLANNING: {AgentState.DONE, AgentState.QUERY_PENDING, AgentState.IDLE},
    AgentState.QUERY_PENDING: {AgentState.PLANNING, AgentState.DONE, AgentState.QUERY_PENDING},
    AgentState.DONE: {AgentState.IDLE, AgentState.PLANNING},
}


def can_transition(src: AgentState, dst: AgentState) -> bool:
    """校验状态流转是否合法"""
    return dst in TRANSITIONS.get(src, set())


# 挂起态查询信息（存 SQLite 的 JSON 结构说明）
# pending_query = {"sql": "...", "purpose": "查询目的", "round": 1}
