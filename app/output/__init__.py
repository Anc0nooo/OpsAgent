"""
④结果输出模块（app/output/）
- formatter：四段总结解析（结论/原因分析/处理步骤/风险提示）、查询卡片、来源引用附加
- exporter：md / csv / .sql 三格式导出
- api：卡片数据接口 + 文件下载接口
"""
from app.output.formatter import build_query_card, build_summary_sections, attach_sources  # noqa: F401
