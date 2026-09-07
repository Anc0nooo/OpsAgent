# OpsAgent 医疗运维 AI 智能体

面向医院信息科的多用户日常运维 AI 助手：用户名密码登录，一问一答 + 方案输出 + 人工在环只读查询。Agent 需要真实数据时停止输出，给出**只读 SELECT SQL**，人工在真实环境执行并**脱敏回传**结果后继续排查（最多 5 轮）。

## 核心特性

- **多用户架构**：用户名密码注册 / 登录（JWT 鉴权，bcrypt 密码哈希），MySQL 8.0 存储全部业务数据；知识库、会话历史、API 配置严格按用户隔离，互不可见；支持上传头像（默认用用户名首字符兜底）
- **RAG 知识库**：文档上传 / 粘贴文本 / 表结构导入（DESC、DDL），混合检索（BM25 + 向量）+ gte-rerank-v2 重排；Chroma collection 按用户划分（`user_{id}_knowledge`），编辑后自动重切分 / 重向量化 / 重建 BM25
- **Agent 规划器**：意图识别 + 状态机（含 `query_pending` 挂起态），上下文截断，SSE 流式打字机输出；方案输出前固定注入「📚 知识库扫描结果」小节
- **只读查询硬约束**：ReadOnlySQLGuard 六层校验——白名单（仅 SELECT/WITH）、写操作黑名单、注释藏写拦截、伪写拦截（FOR UPDATE / SELECT INTO / EXECUTE IMMEDIATE / DBMS_*）、PL/SQL 块拦截、MySQL 语法拦截
- **结果输出**：四段总结卡片（结论 / 原因分析 / 处理步骤 / 风险与验证）、查询卡片、导出 .md / .sql / .csv
- **会话管理**：侧边栏 DeepSeek 风格，按日期分组（置顶 → 今天 → 昨天 → 7 天内 → 更早），支持置顶 / 多选删除 / 实时搜索；会话与消息按用户隔离，越权访问返回 404
- **API Key 按用户配置**：每个用户在设置弹窗配置自己的百炼 Key（存 `user_configs` 表，掩码回显不泄露明文），保存后 LLM 客户端按用户缓存热切换；未配置时回落 `.env` 全局兜底 Key
- **隐私红线**：所有回传入口（查询卡片、导出 .sql、挂起提示、前端输入框）均带脱敏提醒；方案与提示语不诱导贴出完整患者信息

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.11+ · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · MySQL 8.0（PyMySQL）· JWT（PyJWT + bcrypt）· Chroma（本地持久化，按用户 collection） |
| 前端 | Vue 3 · Vite · TypeScript · Element Plus · Vue Router（登录守卫）· axios 拦截器（自动附 token，401 跳登录） |
| 模型 | 阿里百炼（对话 qwen-plus / 向量 text-embedding-v3 / 重排 gte-rerank-v2），对话/向量/重排统一走百炼 |

## 目录结构

```
├── app/
│   ├── rag/          # ① RAG 知识库（入库/检索/重排/表结构解析，Chroma 按用户隔离）
│   ├── agent/        # ② Agent 规划器（状态机/上下文/提示词/会话存储）
│   ├── tools/        # ③ 工具调用（方案/SQL/清单/仿真查询/文档解释）
│   ├── output/       # ④ 结果输出（四段卡片/导出）
│   ├── api/          # 对话 SSE / 健康 / 设置接口
│   ├── auth/         # 用户认证（注册/登录/me/头像 API，JWT 解析，鉴权依赖）
│   ├── db/           # MySQL 引擎 / ORM 模型（users/user_configs/knowledge_docs/conversations/messages）
│   ├── core/         # LLM 客户端（按用户配置缓存）/ ReadOnlySQLGuard
│   ├── models/       # 统一响应模型
│   └── config/       # 全局配置（settings.py）
├── frontend/         # Vue3 前端（登录/对话/知识库 + 侧边栏用户区 + 设置弹窗）
│   └── src/
│       ├── views/        # LoginView（登录/注册）、ChatView、KnowledgeView
│       ├── api/          # request（createAuthHttp 统一鉴权实例）、auth、chat、knowledge、settings
│       ├── store/        # 轻量全局状态（user：用户信息/头像；settings：模型配置状态）
│       └── router/       # 路由 + 登录守卫
├── scripts/          # 建库 / 多用户验证 / 检索测试 / 验收脚本
├── tests/            # 单元测试
├── data/             # Chroma 向量本地数据（自动生成；业务数据在 MySQL）
├── .env              # 环境配置（MySQL / JWT / 百炼兜底 Key）
├── run_local.bat     # Windows 一键启动
└── docker-compose.yml # 容器化部署（预留，暂未纳入 MySQL 服务）
```

## 环境准备

1. **Python 3.11+**：[python.org](https://www.python.org/downloads/) 安装时勾选 *Add to PATH*
2. **Node.js 18+**：[nodejs.org](https://nodejs.org/)（前端构建需要）
3. **MySQL 8.0（必装）**：[dev.mysql.com](https://dev.mysql.com/downloads/installer/) 安装后确保服务运行（Windows 服务名通常为 `MySQL80`）
4. **阿里百炼 API-KEY**：
   - 访问 [dashscope.aliyun.com](https://dashscope.aliyun.com/) → 开通 DashScope
   - 右上角头像 → **API-KEY 管理** → **创建 API-KEY** → 复制
   - 登录应用后在「设置弹窗」按用户配置（也可在 `.env` 填全局兜底 Key）

## 启动步骤

### 1. 配置 .env

项目根目录编辑 `.env`（关键项）：

```ini
# MySQL 8.0（必填）
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的MySQL密码
MYSQL_DATABASE=opsagent

# JWT 鉴权（生产环境务必换成随机 32 位以上字符串）
JWT_SECRET=OpsAgent2026SecretKeyForJwtTokenGenerationAtLeast32Chars
JWT_EXPIRE_DAYS=7

# 阿里百炼（可选：全局兜底 Key；推荐每个用户在设置弹窗里配置自己的 Key）
DASHSCOPE_API_KEY=
```

### 2. 初始化数据库

```bat
:: 创建 opsagent 库（utf8mb4；表结构由后端启动时自动创建/迁移）
python scripts\init_mysql.py

:: 或手动建库
:: mysql> CREATE DATABASE opsagent DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 3. 启动前后端

```bat
:: 后端
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

:: 前端（另开一个终端）
cd frontend
npm install
npm run dev
```

也可双击 `run_local.bat` 一键启动（自动创建虚拟环境、装依赖、起前后端）。

启动后：
- 前端页面：<http://localhost:5173>（未登录自动跳转登录页）
- 后端接口文档：<http://127.0.0.1:8000/docs>

### 4. 首次使用

1. 登录页点击「注册」，输入用户名密码创建账号
2. 登录后设置弹窗会自动弹出（首次），填入你的百炼 API Key 并保存
3. 侧边栏底部显示「API 已配置」（绿色）即就绪；顶部头像点击可上传头像 / 退出登录

> 容器化部署：`docker-compose.yml` 为早期预留版本，尚未纳入 MySQL 服务，多用户版建议直接按上述步骤部署。

## 多用户架构说明

### 数据表（MySQL `opsagent` 库，启动时自动建表/补列）

| 表 | 说明 | 隔离方式 |
| --- | --- | --- |
| `users` | 用户（bcrypt 密码哈希、头像 data URL、状态） | — |
| `user_configs` | 每用户模型配置（api_key / base_url / 各模型名） | `user_id` + `provider` 唯一 |
| `knowledge_docs` | 知识文档元数据 | `user_id` 外键 |
| `knowledge_chunks` | 文档分块 | 通过文档关联隔离 |
| `conversations` | 会话 | `user_id` 外键 |
| `messages` | 消息 | 通过会话间接隔离（查询前校验归属） |

### 隔离规则

- **知识库**：Chroma collection 按用户划分（`user_{id}_knowledge`），检索/写入/删除只在当前用户 collection 内进行；删除文档同时清理对应向量
- **会话**：创建/查询/置顶/删除均校验 `user_id`；跨用户访问他人会话返回 404
- **API Key**：LLM 客户端按 `user_id` 从 `user_configs` 读取配置并缓存实例；用户保存新 Key 后立即失效缓存热切换
- **鉴权**：除注册 / 登录 / 健康检查外，所有接口需 `Authorization: Bearer <token>`；token 无效或过期返回 401，前端自动跳登录页

## 验证与测试

```bat
:: 多用户端到端验证（注册/登录/401/头像/会话隔离/越权拦截，需后端已启动）
.venv\Scripts\python.exe scripts\verify_multiuser.py

:: 配置状态链路验证（保存 Key → 掩码回显 → 未登录 401）
.venv\Scripts\python.exe scripts\verify_config_status.py

:: RAG 检索效果测试（10 个问题，目标命中率 ≥ 70%）
.venv\Scripts\python.exe scripts\test_retrieval.py

:: 单元测试
.venv\Scripts\python.exe -m pytest tests/ -q
```

## 配置说明（.env）

| 配置项 | 说明 | 默认 |
| --- | --- | --- |
| `MYSQL_HOST` / `MYSQL_PORT` | MySQL 地址与端口 | `localhost` / `3306` |
| `MYSQL_USER` / `MYSQL_PASSWORD` | MySQL 账号密码（必填） | `root` / — |
| `MYSQL_DATABASE` | 业务库名（自动建表） | `opsagent` |
| `JWT_SECRET` | JWT 签名密钥（≥32 位随机字符串，生产必改） | 内置默认 |
| `JWT_ALGORITHM` / `JWT_EXPIRE_DAYS` | 签名算法 / token 有效期 | `HS256` / `7` |
| `DASHSCOPE_API_KEY` | 全局兜底 Key（用户未在设置弹窗配置时使用） | 空 |
| `DASHSCOPE_BASE_URL` | 百炼 OpenAI 兼容 base_url | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `CHAT_MODEL` | 对话主模型 | `qwen-plus` |
| `BACKUP_CHAT_MODEL` | 失败自动降级模型（留空不降级） | `qwen-flash` |
| `REASONING_MODEL` | 复杂 bug 推理模型（留空回退 CHAT_MODEL） | 空 |
| `EMBED_MODEL` | 向量模型（固定百炼，勿改） | `text-embedding-v3` |
| `RERANK_MODEL` | 重排模型（gte-rerank 旧版已停用） | `gte-rerank-v2` |
| `SQL_DIALECT` | SQL 方言（锁定 Oracle，请勿修改） | `oracle` |
| `HISTORY_MAX_TURNS` | 上下文保留最近轮数 | `5` |
| `QUERY_MAX_ROUNDS` | 人工在环查询轮次上限 | `5` |
| `PASTE_MAX_LINES` | 回传结果建议行数上限 | `30` |
| `BM25_WEIGHT` / `VECTOR_WEIGHT` | 混合检索融合权重 | `0.5` / `0.5` |

> base_url / 模型名后端固定，不在弹窗暴露；每个用户仅需在设置弹窗配置自己的百炼 API Key（掩码回显）。

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 后端启动报 MySQL 连接失败 | `.env` 的 `MYSQL_PASSWORD` 不对，或 MySQL80 服务未启动（`services.msc` 里检查）；先用 `python scripts\init_mysql.py` 验证连通 |
| 报 `Unknown database 'opsagent'` | 库未创建：运行 `python scripts\init_mysql.py` 或手动 `CREATE DATABASE opsagent DEFAULT CHARACTER SET utf8mb4` |
| 前端一直跳登录页 / 提示登录过期 | 未登录或 token 过期（默认 7 天），重新登录即可；后端重启不清 token（JWT_SECRET 未变时） |
| 登录后知识库 / 会话仍报 401 | 浏览器缓存了旧前端代码，强制刷新（Ctrl+F5）；确认前端跑在 5173 端口（后端 CORS 仅放行 5173） |
| 侧边栏显示「API 未配置」 | 当前账号未配置 Key：打开设置弹窗保存百炼 API Key，保存后状态实时变绿 |
| 保存的 API Key 不生效 | 确认保存时输入框有值（已配置状态下输入框为占位符，聚焦清空后输入新值）；保存成功后侧边栏应变绿 |
| 前端提示「后端未连接」 | 后端 8000 端口未启动；先看后端窗口报错 |
| 8000 / 5173 端口被占用 | 关闭占用进程（注意残留的 uvicorn 子进程），或改 `.env` 的 `PORT` 与 `frontend/vite.config.ts` |
| 对话报 `LLM API 调用失败` | 检查当前账号配置的 API-KEY 有效性与网络；核对模型名是否在百炼已开通 |
| 知识检索不准 | 在知识库管理页补充对应文档 / 表结构（文档按账号隔离，先确认登录的是上传文档的账号） |
| 上传文档报 `Error loading hnsw index` | Chroma 索引损坏（异常退出 / 版本混用）。后端启动时自动健康检查修复；仍失败则停止后端后将 `data/chroma` 改名留底再重启 |
| 忘记密码 | 当前版本无找回流程，可由管理员在 `users` 表重置该用户 `password_hash`（bcrypt 格式） |

## 隐私与安全注意事项

1. **只读约束**：Agent 不直连生产库、不执行任何命令；生成的 SQL 经 ReadOnlySQLGuard 强校验（写操作 / 伪写 / MySQL 语法一律拒绝），需人工复制到真实环境执行
2. **脱敏红线**：涉及患者数据的查询结果，回传前务必脱敏（姓名/ID/联系方式打码），单次回传建议 ≤30 行；系统在所有回传入口均有提醒
3. **账号与数据安全**：密码仅存 bcrypt 哈希；业务数据在 MySQL 按用户隔离，向量数据在 Chroma 按用户 collection 隔离；接口越权访问他人会话返回 404
4. **API-KEY 安全**：每个用户的 Key 只存自己的 `user_configs` 行；接口仅返回掩码 + `has_api_key` 标志，不回显明文；日志不打印密钥；如怀疑泄露请立即在百炼控制台吊销重建
5. **配置安全**：`.env` 含 MySQL 密码与 JWT 密钥，**切勿提交代码仓库**；生产环境务必更换 `JWT_SECRET`
