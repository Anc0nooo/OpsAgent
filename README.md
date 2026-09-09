# OpsAgent 医疗运维 AI 智能体

面向医院信息科的多用户日常运维 AI 助手：用户名密码登录，一问一答 + 方案输出 + 人工在环只读查询。Agent 需要真实数据时停止输出，给出**只读 SELECT SQL**，人工在真实环境执行并**脱敏回传**结果后继续排查（最多 5 轮）。PC 与手机浏览器均可使用，移动端侧边栏抽屉化、对话页适配软键盘。

## 核心特性

- **多用户架构**：用户名密码注册 / 登录（JWT 鉴权，bcrypt 密码哈希），MySQL 8.0 存储全部业务数据；知识库、会话历史、API 配置严格按用户隔离，互不可见；支持上传头像（默认用用户名首字符兜底）
- **RAG 知识库**：文档上传（PDF/DOCX）/ 粘贴文本 / 表结构导入（DESC、DDL），混合检索（BM25 + 向量）+ gte-rerank-v2 重排；Chroma collection 按用户划分（`user_{id}_knowledge`），编辑后自动重切分 / 重向量化 / 重建 BM25
- **Agent 规划器**：意图识别（闲聊 / 工作）+ 状态机（含 `query_pending` 挂起态），上下文裁剪，SSE 流式打字机输出；方案输出前固定注入「知识库扫描结果」小节
- **指代消解**：用户用省略句 / 短问法（"总共有多少字段""还有哪些"）承接上文时，自动结合对话历史锁定所指对象（具体表 / 文档 / 话题），避免误把"那张表的字段数"答成"整个 PDF 的字段数"
- **只读查询硬约束**：ReadOnlySQLGuard 六层校验——白名单（仅 SELECT/WITH）、写操作黑名单、注释藏写拦截、伪写拦截（FOR UPDATE / SELECT INTO / EXECUTE IMMEDIATE / DBMS_*）、PL/SQL 块拦截、MySQL 语法拦截
- **结果输出**：四段总结卡片（结论 / 原因分析 / 处理步骤 / 风险与验证）、查询卡片、导出 .md / .sql / .csv
- **移动端自适应**：< 768px 自动切换移动布局——侧边栏改为 Vant 抽屉（汉堡按钮呼出）、对话气泡 85% 宽、输入区跟随软键盘（visualViewport）不被遮挡、代码块/表格横向滚动、知识库三栏变单列并带悬浮上传按钮、确认框切 Vant Dialog、触控热区 ≥44px
- **会话管理**：侧边栏 DeepSeek 风格，按日期分组（置顶 → 今天 → 昨天 → 7 天内 → 更早），支持置顶 / 多选删除 / 实时搜索；会话与消息按用户隔离，越权访问返回 404
- **管理后台**：管理员（ancon 角色）可管理全部用户（禁用 / 启用 / 改角色 / 重置密码 / 删除，操作列主按钮 + 下拉菜单）、按条件筛选操作日志、导出 CSV；首个注册用户自动成为管理员，也可在 `.env` 中通过 `ADMIN_USERNAME` 指定
- **API Key 按用户配置**：每个用户在设置弹窗配置自己的百炼 Key（存 `user_configs` 表，掩码回显不泄露明文），保存后 LLM 客户端按用户缓存热切换；未配置时回落 `.env` 全局兜底 Key
- **隐私红线**：所有回传入口（查询卡片、导出 .sql、挂起提示、前端输入框）均带脱敏提醒；方案与提示语不诱导贴出完整患者信息

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.11+ · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · MySQL 8.0（PyMySQL）· JWT（PyJWT + bcrypt）· Chroma（本地持久化，按用户 collection） |
| 前端（PC） | Vue 3 · Vite · TypeScript · Element Plus · Vue Router（登录/管理员守卫）· axios 拦截器（自动附 token，401 跳登录） |
| 前端（移动） | Vant 4（抽屉 / 悬浮按钮 / Dialog）· postcss-px-to-viewport（vw 适配，设计稿 375px，**仅转换 Vant 样式，PC 像素零影响**）· visualViewport 软键盘适配 |
| 模型 | 阿里百炼（对话 qwen-plus / 向量 text-embedding-v3 / 重排 gte-rerank-v2），对话/向量/重排统一走百炼 |

## 目录结构

```
├── app/
│   ├── main.py           # FastAPI 入口：注册路由 + startup 初始化 MySQL/RAG/会话存储
│   ├── rag/              # ① RAG 知识库（入库 / 检索 / 重排 / 表结构解析，Chroma 按用户隔离）
│   ├── agent/            # ② Agent 规划器（状态机 / 上下文 / 提示词 / 会话存储）
│   │   ├── planner.py    # 意图识别 → RAG → 单次结构化调用 → 挂起 / 收敛
│   │   ├── prompts.py    # SYSTEM/INTENT/CHAT/PLAN/CONTINUE 提示词（含指代消解规则）
│   │   ├── context.py    # 历史裁剪（轮数 × 2 条）+ RAG 注入格式化（句边界截断）
│   │   └── store.py      # conversations / messages 表 CRUD（按 user_id 隔离）
│   ├── tools/            # ③ 工具调用（方案 / SQL / 清单 / 仿真查询 / 文档解释）
│   ├── output/           # ④ 结果输出（四段卡片 / 导出 .md/.sql/.csv）
│   ├── api/              # chat（SSE 流式）/ settings（模型配置）/ admin（用户 + 日志）
│   ├── auth/             # 用户认证（注册 / 登录 / me / 头像，JWT 解析，鉴权依赖）
│   ├── db/               # MySQL 引擎 / ORM 模型（含 operation_logs 审计表）
│   ├── core/             # LLM 客户端（按用户配置缓存）/ ReadOnlySQLGuard
│   └── config/           # 全局配置（settings.py，从 .env 读取）
├── frontend/             # Vue3 前端（PC + 移动端同一套代码）
│   ├── vite.config.ts    # postcss-px-to-viewport：375px 基准，仅转 Vant
│   └── src/
│       ├── views/         # Login / Chat / Knowledge / Admin / History 五个主页面
│       ├── components/    # AppSidebar（移动端进 Vant 抽屉）/ SettingsDialog 等
│       ├── api/           # request（鉴权实例）/ auth / chat / knowledge / admin / settings
│       ├── store/         # 轻量全局状态（user / settings）
│       └── router/        # 路由 + 登录守卫 + 管理员守卫
├── scripts/              # 建库 / 多用户验证 / 检索测试 / 管理员验证 / 诊断脚本
├── tests/                # 单元测试
├── data/                 # Chroma 向量本地数据（自动生成；业务数据在 MySQL）
├── .env                  # 环境配置（MySQL / JWT / 百炼兜底 Key）
├── run_local.bat         # Windows 一键启动
└── docker-compose.yml    # 容器化部署（预留，暂未纳入 MySQL 服务）
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
# token 有效期（小时）：登录一次后 5 小时需重新登录
JWT_EXPIRE_HOURS=5
# 调试用：>0 时按分钟过期（如 1 便于自测过期跳转），正式环境留 0
JWT_EXPIRE_MINUTES=0

# 阿里百炼（可选：全局兜底 Key；推荐每个用户在设置弹窗里配置自己的 Key）
DASHSCOPE_API_KEY=

# 首个管理员指定（可选；不填则首个注册用户自动成为管理员）
ADMIN_USERNAME=
```

### 2. 初始化数据库

```bat
:: 创建 opsagent 库（utf8mb4；表结构由后端启动时自动创建 / 迁移）
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
- 前端页面：<http://localhost:5173>（未登录自动跳转登录页；手机同网段访问 `http://<电脑IP>:5173`）
- 后端接口文档：<http://127.0.0.1:8000/docs>

### 4. 首次使用

1. 登录页点击「注册」，输入用户名密码创建账号（首个用户自动成为管理员）
2. 登录后设置弹窗会自动弹出（首次），填入你的百炼 API Key 并保存
3. 侧边栏底部显示「API 已配置」（绿色）即就绪；顶部头像点击可上传头像 / 退出登录
4. 管理员可在 `/admin` 页面管理用户与查看操作日志

> 容器化部署：`docker-compose.yml` 为早期预留版本，尚未纳入 MySQL 服务，多用户版建议直接按上述步骤部署。

## 移动端适配说明

断点 **768px**：≥768px 为 PC 布局，<768px 自动切移动布局，同一套 Vue 代码无独立站点。

| 适配点 | 实现方式 |
| --- | --- |
| 视口 | viewport meta 禁缩放 + `viewport-fit=cover`（刘海屏安全区 `env(safe-area-inset-*)`） |
| 尺寸缩放 | postcss-px-to-viewport（375px 设计稿），**exclude 全部 src 与非 Vant 的 node_modules**，只把 Vant 组件 px 转 vw；项目自身样式 px 原样保留，PC 布局像素级不变 |
| 软键盘 | `visualViewport` 监听写入 `--app-height`，移动端布局高度用它代替 `100vh`，输入框始终在键盘上方 |
| 侧边栏 | 移动端改为 `van-popup position="left"` 抽屉（85vw / 最大 320px），顶栏汉堡按钮呼出，选会话 / 新建后自动收起 |
| 对话页 | 用户气泡 max-width 85%；发送按钮 44px；代码块 / markdown 表格横向滚动；挂起 SQL 卡片纵向紧凑 |
| 知识库 | 三栏 grid 在移动端变单列且顺序为「上传 → 文档列表 → 检索测试」；`van-floating-bubble` 悬浮 + 号直接拉起文件选择 |
| 管理后台 | 表格包横滚容器（保持 640px 最小可读宽度，超宽横滚） |
| 弹窗/确认 | 危险操作确认移动端走 Vant `showConfirmDialog`（红色确定按钮）；el-dialog 窄屏宽度统一约束 |
| 触控 | 按钮 / 输入框最小 44~48px 热区；输入框 16px 字号防 iOS 聚焦自动放大；去掉点击灰色高亮 |

## 多用户架构说明

### 数据表（MySQL `opsagent` 库，启动时自动建表 / 补列）

| 表 | 说明 | 隔离方式 |
| --- | --- | --- |
| `users` | 用户（bcrypt 密码哈希、头像 data URL、状态、角色） | — |
| `user_configs` | 每用户模型配置（api_key / base_url / 各模型名） | `user_id` + `provider` 唯一 |
| `knowledge_docs` | 知识文档元数据 | `user_id` 外键 |
| `knowledge_chunks` | 文档分块 | 通过文档关联隔离 |
| `conversations` | 会话（状态机 / 挂起查询 / 轮次 / 置顶） | `user_id` 外键 |
| `messages` | 消息（user / assistant） | 通过会话间接隔离（查询前校验归属） |
| `operation_logs` | 操作审计日志（登录 / 对话 / 上传 / 删除 / 配置保存等） | `user_id` 外键 |

### 隔离规则

- **知识库**：Chroma collection 按用户划分（`user_{id}_knowledge`），检索 / 写入 / 删除只在当前用户 collection 内进行；删除文档同时清理对应向量；删除用户时级联清理其全部数据（含 Chroma 向量）
- **会话**：创建 / 查询 / 置顶 / 删除均校验 `user_id`；跨用户访问他人会话返回 404
- **API Key**：LLM 客户端按 `user_id` 从 `user_configs` 读取配置并缓存实例；用户保存新 Key 后立即失效缓存热切换
- **鉴权**：除注册 / 登录 / 健康检查外，所有接口需 `Authorization: Bearer <token>`；token 无效或过期返回 401，前端自动跳登录页
- **角色与权限**：`ancon`（管理员）可见 `/admin` 后台入口，能管理所有用户、查看全部操作日志、导出 CSV；`user`（普通用户）看不到后台入口，访问 `/admin` 会被自动跳回首页

## Agent 工作流

```
用户输入
   │
   ├─ 意图识别（chat / work）
   │     └─ chat → 闲聊直答（不检索、不挂起）
   │
   └─ work
         ├─ RAG 双路检索（普通知识 + 表结构）
         ├─ 单次结构化调用 PLAN_PROMPT
         │     ├─ 指代消解（短问法 → 锁定上文对象）
         │     ├─ 任务分流（SQL / 排查 / 知识问答 / 闲聊）
         │     └─ 输出 JSON（need_query / sql / answer / 自评三件套）
         │
         ├─ need_query=false → 直接输出 answer（DONE）
         └─ need_query=true  → 挂起 query_pending
                                 ├─ 用户回传 SQL 结果 → 继续分析（最多 5 轮）
                                 └─ 达上限 → 强制收敛
```

## 验证与测试

```bat
:: 多用户端到端验证（注册 / 登录 / 401 / 头像 / 会话隔离 / 越权拦截）
.venv\Scripts\python.exe scripts\verify_multiuser.py

:: 管理员功能验证（用户管理 / 日志查看 / 权限隔离）
.venv\Scripts\python.exe scripts\verify_admin.py

:: 配置状态链路验证（保存 Key → 掩码回显 → 未登录 401）
.venv\Scripts\python.exe scripts\verify_config_status.py

:: RAG 检索效果测试（10 个问题，目标命中率 ≥ 70%）
.venv\Scripts\python.exe scripts\test_retrieval.py

:: Agent 规划器测试
.venv\Scripts\python.exe scripts\test_planner.py

:: 单元测试
.venv\Scripts\python.exe -m pytest tests/ -q
```

前端移动端验收：`npm run dev` 后 Chrome DevTools 切 iPhone 12/14 Pro 预览，或手机浏览器直接访问；重点验证无横向滚动条、对话输入框不被软键盘遮挡、抽屉呼出 / 收起正常。

## 配置说明（.env）

| 配置项 | 说明 | 默认 |
| --- | --- | --- |
| `MYSQL_HOST` / `MYSQL_PORT` | MySQL 地址与端口 | `localhost` / `3306` |
| `MYSQL_USER` / `MYSQL_PASSWORD` | MySQL 账号密码（必填） | `root` / — |
| `MYSQL_DATABASE` | 业务库名（自动建表） | `opsagent` |
| `JWT_SECRET` | JWT 签名密钥（≥32 位随机字符串，生产必改） | 内置默认 |
| `JWT_ALGORITHM` / `JWT_EXPIRE_HOURS` | 签名算法 / token 有效期（小时，默认 5 小时后需重新登录） | `HS256` / `5` |
| `JWT_EXPIRE_MINUTES` | 调试用：>0 时按分钟过期（自测过期跳转用），正式环境留 `0` | `0` |
| `ADMIN_USERNAME` | 指定首个管理员用户名（可选） | 空（首个注册者自动成为管理员） |
| `DASHSCOPE_API_KEY` | 全局兜底 Key（用户未在设置弹窗配置时使用） | 空 |
| `DASHSCOPE_BASE_URL` | 百炼 OpenAI 兼容 base_url | `https://dashscope.aliyun.com/compatible-mode/v1` |
| `CHAT_MODEL` | 对话主模型 | `qwen-plus` |
| `BACKUP_CHAT_MODEL` | 失败自动降级模型（留空不降级） | `qwen-flash` |
| `REASONING_MODEL` | 复杂 bug 推理模型（留空回退 CHAT_MODEL） | 空 |
| `EMBED_MODEL` | 向量模型（固定百炼，勿改） | `text-embedding-v3` |
| `RERANK_MODEL` | 重排模型（gte-rerank 旧版已停用） | `gte-rerank-v2` |
| `SQL_DIALECT` | SQL 方言（锁定 Oracle，请勿修改） | `oracle` |
| `HISTORY_MAX_TURNS` | 上下文保留最近轮数（1 轮 = user + assistant） | `8` |
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
| 手机访问不到页面 | 手机与电脑需同一 Wi-Fi，访问 `http://<电脑局域网IP>:5173`；确认 Windows 防火墙放行 5173 端口 |
| 手机上点输入框页面放大 | iOS Safari 对 <16px 输入框聚焦会自动放大；移动端输入框已统一 16px，若仍出现请确认浏览器缓存已刷新 |
| 侧边栏显示「API 未配置」 | 当前账号未配置 Key：打开设置弹窗保存百炼 API Key，保存后状态实时变绿 |
| 保存的 API Key 不生效 | 确认保存时输入框有值（已配置状态下输入框为占位符，聚焦清空后输入新值）；保存成功后侧边栏应变绿 |
| 前端提示「后端未连接」 | 后端 8000 端口未启动；先看后端窗口报错 |
| 8000 / 5173 端口被占用 | 关闭占用进程（注意残留的 uvicorn 子进程），或改 `.env` 的 `PORT` 与 `frontend/vite.config.ts` |
| 对话报 `LLM API 调用失败` | 检查当前账号配置的 API-KEY 有效性与网络；核对模型名是否在百炼已开通 |
| 知识检索不准 | 在知识库管理页补充对应文档 / 表结构（文档按账号隔离，先确认登录的是上传文档的账号） |
| 对话承接上文时答非所问 | 确认会话未被删除（历史存在才能指代消解）；问题太短且历史里出现多个候选对象时，Agent 会反问确认，按提示补充即可 |
| 上传文档报 `Error loading hnsw index` | Chroma 索引损坏（异常退出 / 版本混用）。后端启动时自动健康检查修复；仍失败则停止后端后将 `data/chroma` 改名留底再重启 |
| 忘记密码 | 当前版本无找回流程；管理员可在 `/admin` 后台重置该用户密码，或由 DBA 在 `users` 表重置 `password_hash`（bcrypt 格式） |

## 隐私与安全注意事项

1. **只读约束**：Agent 不直连生产库、不执行任何命令；生成的 SQL 经 ReadOnlySQLGuard 强校验（写操作 / 伪写 / MySQL 语法一律拒绝），需人工复制到真实环境执行
2. **脱敏红线**：涉及患者数据的查询结果，回传前务必脱敏（姓名 / ID / 联系方式打码），单次回传建议 ≤30 行；系统在所有回传入口均有提醒
3. **账号与数据安全**：密码仅存 bcrypt 哈希；业务数据在 MySQL 按用户隔离，向量数据在 Chroma 按用户 collection 隔离；接口越权访问他人会话返回 404；删除用户时级联清理其全部数据
4. **API-KEY 安全**：每个用户的 Key 只存自己的 `user_configs` 行；接口仅返回掩码 + `has_api_key` 标志，不回显明文；日志不打印密钥；如怀疑泄露请立即在百炼控制台吊销重建
5. **配置安全**：`.env` 含 MySQL 密码与 JWT 密钥，**切勿提交代码仓库**；生产环境务必更换 `JWT_SECRET`
6. **审计留痕**：关键操作（登录 / 注册 / 对话 / 上传 / 删除 / 检索 / 配置保存 / 用户管理）均记录到 `operation_logs`，含用户名、操作类型、详情摘要、IP 与时间，管理员可在后台按条件筛选导出
