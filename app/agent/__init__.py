"""
②Agent 规划器模块
- 状态机：IDLE → PLANNING → QUERY_PENDING（人工在环挂起）→ DONE，多轮查询上限 5 轮；
- 意图识别、上下文裁剪、RAG 检索注入；
- 编排：新问题（检索→方案/挂起）与挂起续推（回传→继续分析→收敛）。
"""
from app.agent.planner import planner  # noqa: F401 全局规划器单例
