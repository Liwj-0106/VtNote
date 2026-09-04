# VtNote 技术方案与决策

状态：当前实现基线
校准日期：2026-08-30

## 总体架构

```text
Browser / React SPA
        │ same-origin HTTP + CSRF
        ▼
FastAPI API ──────────────── Windows Credential Manager
        │                           │ credential_ref
        │ SQLite WAL + durable files│
        ▼                           ▼
     SQLite  ◀────────────── Independent Worker
        │                       │ source / transcribe / notes
        │                       ├─ controlled yt-dlp + pinned HTTPS
        │                       ├─ FFmpeg / FFprobe
        │                       ├─ Tencent recording ASR / private COS
        │                       ├─ SenseVoice Small / sherpa-onnx / Silero VAD
        │                       ├─ faster-whisper / CTranslate2 / CUDA
        │                       └─ Tencent TokenHub
        ▼
Data root + Cache root
```

`src/vtnote/launcher.py` 的 supervisor 启动 API 和 Worker 两个子进程。Worker 内还有模型安装与 maintenance 循环。API 不执行长任务；两个进程只通过 SQLite、规范文件和登记后的 runtime 资产交接。

### 代码组织与依赖方向

当前采用渐进式模块化，不做一次性目录搬迁：稳定的 `vtnote.api`、`vtnote.tasks`、
`vtnote.configuration` 仍是兼容门面，新职责按边界进入专用模块。

```text
launcher.py / api.py                 composition root
        │
        ├── http/                    请求合同、响应序列化、路由
        ├── application/             无 ORM 的应用合同与公共视图
        ├── tasks.py / configuration.py / worker.py
        │                            用例编排与事务入口
        ├── *_policy.py / pipeline.py / schemas.py
        │                            纯校验、状态规则和规范数据
        └── models.py / database.py / media.py / platform_* / tencent_*
                                     持久化和外部适配器
```

- HTTP 类型不再定义在 `create_app` 中；核心模块禁止反向导入 `vtnote.http`。
- `application/` 只保存跨入口共享的合同，不依赖 SQLAlchemy/ORM。
- 高风险文件操作使用独立服务，例如 `task_deletion.py`；`TaskService` 保持公共 API，
  但不继续吸收每一种生命周期实现。
- 前端页面负责路由级数据编排，可复用的状态模型和 UI 进入
  `frontend/src/features/<capability>/`；通用无业务组件仍放在 `components/`。
- `tests/test_architecture_boundaries.py` 固化依赖边界和历史热点文件预算。预算不是追求
  小文件数量，而是要求新增职责先拆边界再进入组合入口。

## 技术选型

| 层 | 当前选择 | 原因 |
|---|---|---|
| UI | React 19 + TypeScript + Vite + GSAP 3.15.0 | 轻量本地 SPA、类型化 API；GSAP 只承载可中断的处理状态动效 |
| API | FastAPI + Uvicorn + Pydantic 2 | 明确请求合同、统一错误、上传流和本地 HTTP 服务 |
| 持久化 | SQLAlchemy 2 + SQLite WAL | 单用户本机部署无需独立数据库，同时支持事务、索引、租约和恢复 |
| 任务执行 | 独立 Worker + DB lease | API 重启不丢任务；避免进程内队列和 `BackgroundTasks` 的不耐久性 |
| 媒体 | FFmpeg/FFprobe | 统一探测、抽音频、转码、规范采样率和音频导出 |
| 平台 | yt-dlp adapter + 自有安全传输 | 隔离易变平台逻辑，固定版本，限制 URL/DNS/重定向/运行时 |
| 云 ASR | 腾讯云录音文件识别 | 异步 TaskId 可持久查询；支持内联和私有 COS 两条路径 |
| 本地 ASR | SenseVoice Small INT8 + sherpa-onnx + Silero VAD；Faster-Whisper + CTranslate2 | 用户选择固定引擎；SenseVoice 走 CPU 且不启用说话人分离，Faster-Whisper 保留 GPU/显式 CPU 路径 |
| AI | TokenHub `glm-5.1` | 当前产品 UI 的国内模型入口；用户显式选择才调用 |
| 密钥 | `keyring` / Windows Credential Manager | 数据库只保存不透明引用，密钥不随备份或 API 返回 |

后端仍包含百炼、翻译和隐藏管理页面的兼容/实验代码。它们不是当前 UI 基线，是否保留应在单独的产品决策中处理，不能只删除测试而留下未保护的生产分支。YouTube 和抖音公开单视频已经进入当前平台适配基线。

应用首屏继续承担“新总结”启动台，而不是新增营销页。视觉使用暖纸色、墨色正文、细边框和单一低饱和铜色强调；链接、上传、总结设置和主操作收敛在一个工作区。GSAP 只在来源预检和提交状态中驱动 `transform`/`opacity`，状态结束即清理，并在 `prefers-reduced-motion` 下取消时间线。视觉与交互来源记录在[产品与技术调研](reference-projects.md#界面与动效参考)，实现规则以 `design-system/` 和共享前端组件为准。

## 任务与状态流

1. API 校验来源和选项；B 站合集/列表先通过有界适配器分页枚举，用户选择后在一个事务中创建多个独立任务。每个任务再冻结 ASR/AI profile 修订、授权指纹和模型参数。
2. 根据 `output_type` 创建最小阶段集：`audio`、`transcript` 或 `notes`。
3. Worker 以 `BEGIN IMMEDIATE` 和依赖检查领取下一阶段，写入 lease/heartbeat。
4. 来源阶段优先平台字幕；否则发布受控媒体资产。
5. 字幕阶段通过云或本地 ASR 发布不可变 `transcript.json`。
6. 笔记/翻译只读取 transcript 派生新成果。
7. 终态聚合到 item/task；可用成果独立保留并按需导出。

云 ASR 的 create/query 状态单独持久化。请求超时且结果未知时保持 unknown，不盲目再次提交。腾讯查询退避使用 4/8/16/30 秒封顶并加确定性毫秒抖动。

阶段状态转换以数据库为唯一真相：

| 起点 | 事件 | 终点 | 恢复语义 |
|---|---|---|---|
| `queued` | Worker 原子领取并写 lease | `running` | 同一 attempt 只有租约持有者可提交结果 |
| `running` | 成功或业务跳过 | `completed` / `skipped` | 规范成果原子发布，后继阶段才可领取 |
| `running` | 云请求已获得可查询身份 | `waiting_external` | 释放 Worker lease，由 reconciler 按 TaskId 查询 |
| `waiting_external` | 云端终态或结果过期 | `queued` | 唤醒同一 attempt，只做结果发布/失败处理，不重复 create |
| `running` | 临时资源不可用 | `queued` | 释放租约后再领取，不增加 attempt |
| `running` | 安全错误 | `failed` | 保存错误码、上游回退原因和已有成果 |
| 活动状态 | 用户取消 | `cancel_requested → canceled` | cooperative checkpoint 或过期 lease 完成取消 |
| `running` | lease 过期 | `queued` | `recovered_count + 1`，旧 Worker 的写入因 token 失效被拒绝 |
| `failed/canceled` | 用户显式重试 | 新 attempt `queued` | 保留旧 attempt，不原地改写历史 |

`auto` 字幕识别路径固定为“平台字幕 → 腾讯云 ASR → 用户选择的本地 ASR”。云端失败允许回退时，系统先检查所选引擎的固定模型、运行时和必要 VAD，再转 PCM；如果本地也不可用，阶段保持 `failed`，同时记录云端回退原因与本地错误。普通失败可按原配置或仅本地重试；`submission_unknown` 只能选本地，或确认可能重复计费后再次云提交。

## 数据与存储

### 默认位置

源码工作区将长期数据、运行缓存和受管资源固定在仓库自身的 `.vtnote/Data`、
`.vtnote/Cache` 和 `.vtnote/ManagedAssets`。路径从源码位置解析，不依赖启动时的工作
目录；旧用户环境变量若指向项目外，会由源码入口替换为项目内默认值。测试仍可通过
三个 `VTNOTE_*_ROOT` 变量使用 `.vtnote` 内的隔离子目录。

`config.platform_storage_roots` 保留各平台安装包的原生用户目录计算合同，但当前完整
产品和迁移后的源码运行只以 Windows 项目内目录验收；该兼容函数不代表 macOS/Linux
的 DPAPI、本地模型或发行环境已经完成验收。

三个根必须绝对、互不包含；Data 与 Cache 在 Windows 上必须同盘，以保证受控移动和
路径所有权。ManagedAssets 独立保存本地 ASR 模型、VAD、模型安装暂存和 YouTube Deno，
模型暂存与发布保持在同一文件系统内以支持原子移动。

平台请求默认使用 DNS 固定的 HTTPS 直连。受限网络可以显式设置
`VTNOTE_PLATFORM_PROXY_URL`，但配置只接受无认证的回环 HTTP CONNECT 代理。
代理模式仍先执行平台/阶段域名白名单和重定向校验，再对目标域名建立端到端 TLS；
连接对端必须是回环地址。系统 `HTTP_PROXY`/`HTTPS_PROXY` 不会被隐式采用。
YouTube 元数据 POST 仅开放给固定 `youtubei` API 路径/辅助域名，正文有 2 MiB 上限，
`Content-Length` 由传输层重建。

### Data：长期数据

```text
Data/
├── vtnote.db               # 运行时还会出现 -wal / -shm
├── items/<item-id>/
│   ├── source/original.*   # 验证后的原始字幕字节
│   ├── transcript.json     # 唯一规范字幕，不可变
│   ├── recovery/*.json     # 本地 ASR 发布恢复副本
│   ├── translations/*.json
│   └── notes/*.md
└── models/large-v3-turbo/<revision>/
```

SQLite 保存：

- tasks/items/stage runs、阶段依赖、attempt、lease、heartbeat、进度和安全错误；
- 输入类型和 locator、标题、生成选项和不可变 pipeline snapshot；
- 云 submission、外部 task/request id、音频 SHA、COS 对象元数据、查询与清理计划；
- runtime 资产相对路径、角色、大小、SHA-256、active/trash 状态和清理审计；
- provider 连接/profile 元数据、测试状态、修订和授权指纹；
- 默认设置、模型安装状态、凭据清理补偿队列；
- 内容库合集、标签、时间戳摘录和 FTS5 派生搜索文档；
- DPAPI 保护的自定义提示词 envelope。

Data 当前没有任务级自动过期。内容库支持终态任务单条/批量永久删除：先以 SQLite `BEGIN IMMEDIATE` 锁定并完整校验批次，再把对应 Data/Cache 项目目录原子移动到同盘内部 staging，数据库提交失败时恢复文件，提交成功后清除 staging。处理中、仍持有阶段租约，或云 submission/COS 清理未完成的任务拒绝删除；批量请求全有或全无。用户本地原始文件不属于删除范围。停止应用后备份整个 Data；不要在 WAL 活动时只复制 `vtnote.db`。

全文检索使用 SQLite FTS5 `trigram`，索引标题、来源、逐段字幕、总结和摘录；短查询回退到有界 `LIKE`。索引按 item 产物时间与摘录修订生成指纹并惰性重建，不是备份来源。导出目录路径存于本地 `default_settings`；默认指向仓库 `exports/`，用户可通过原生目录选择器改为已有绝对目录。导出以不覆盖策略生成文件，自定义目录不进入缓存清理或任务删除范围。

### Cache：媒体与运行资产

```text
Cache/
├── incoming/<upload-id>/upload.*
├── items/<item-id>/source/upload.*
├── items/<item-id>/audio/
│   ├── downloaded.*
│   ├── cloud.ogg / cloud-inline.ogg / local.wav
│   ├── export.m4a / export.mp3
│   └── staging/*
├── trash/<asset-id>/asset.*
├── task-deletions/<operation-id>/...  # 删除事务的短暂回滚 staging
├── model-installs/...
├── logs/{api,worker,supervisor}.log
├── test/{pytest,playwright}/...
├── package/{builds,frontend,smoke}/...
├── yt-dlp/...
└── youtube-runtime/...
```

需要特别区分：

- trash 资产通常在进入回收站 24 小时后由 maintenance 永久删除；profile 测试样本 1 小时后进入回收流程。
- 日志每个进程 5 MiB、3 个备份。
- COS 对象在 provider 终态后安排删除；删除失败会重试。
- 内容库删除只接受终态任务，并在清理云端临时对象前拒绝删除，避免丢失远端清理计划。
- active 上传副本、下载音频和音频导出目前没有通用 TTL，可能是内容库唯一可导出的音频成果。
- 直接删除 Cache 会造成音频成果丢失，并可能留下 SQLite runtime 记录指向缺失文件，因此“Cache 可随时重建”不是当前合同。
- 平台下载失败遗留的未登记 partial 文件、模型安装 trash 尚无完整自动清理，属于待修复运维缺口。

### 密钥与浏览器数据

- 腾讯 SecretId/SecretKey、TokenHub/Bailian API Key：Windows Credential Manager 的 `VtNote` service；数据库只存 `connection:<uuid>`。
- 自定义提示词：当前用户 DPAPI 密文，位于 defaults/task snapshot。
- 总结提示词分为应用固定的 system 合同和用户可配置的 `task_instruction`；字幕与 map/reduce 中间节点只是不可信证据。用户模板可以改变重点和组织，不能取消严格 JSON schema、输出语言、引用血统校验或原文事实边界。
- 浏览器 localStorage：侧栏折叠状态和 `vtnote.preferences.v1`；不保存任务、字幕、密钥或本地路径。

## 关键技术决策

### ADR-001：本机回环、同源部署

决定：API 固定 `127.0.0.1`，FastAPI 同源提供构建后的 SPA；启用 Host、Origin、CSRF 检查。

理由：当前产品是单用户本机工具，不需要公网身份、CORS 或反向代理。若未来云化，需要单独设计认证、多租户、PostgreSQL/对象存储和数据保留，不能把旧候选云文档当成已实现方案。

### ADR-002：SQLite WAL + 独立 Worker

决定：任务、阶段和外部提交都持久化；Worker 用租约和心跳领取，API 不运行长任务。

理由：本机部署简单，同时提供崩溃恢复、并发互斥和付费请求防重。任务规模超过单机、需要多 Worker 时再评估 PostgreSQL。

### ADR-003：规范文本长期保存，媒体登记为 runtime 资产

决定：`transcript.json` 是唯一字幕真相；SRT/TXT/VTT/Markdown 在请求时生成。媒体由 runtime 资产表登记，不把任意路径当作所有权证明。

理由：避免多份文本漂移，限制路径删除范围，并保留失败任务的部分成果。后续应补 active 媒体保留策略和用户可见的清理入口。

### ADR-004：平台字幕优先，ASR 后备

决定：来源 adapter 先枚举平台字幕，验证并规范化；缺失时才下载音频。

理由：平台字幕通常更快且无额外 ASR 成本。平台接口易变，必须隔离 adapter，并在失败时明确进入后备路径。

### ADR-005：腾讯异步 ASR + 可选择的本地引擎

决定：[腾讯云录音文件识别文档](https://cloud.tencent.com/document/product/1093/37823)的本地文件内联边界按十进制 5,000,000 字节执行。无 COS 时按探测时长自适应编码 Opus CBR，只使用上限的 75% 计算目标码率，再以实际文件字节数作最终预检；有 COS 时使用广州私有对象。本地后备按任务快照选择 SenseVoice Small INT8 或 Faster-Whisper。SenseVoice 同时校验模型、Silero VAD 与 sherpa-onnx CPU 运行时；Faster-Whisper 校验固定模型和许可的 CUDA/CPU 运行时。确认可用后才转 PCM，并保存可校验恢复点。

理由：异步 TaskId 支持可靠查询。75% 余量覆盖原始 AAC 时长元数据偏短和容器开销；最终字节预检仍是权威判断。系统不会在任务执行时临时下载未登记模型。SenseVoice 走 CPU 转写并关闭说话人分离；Faster-Whisper 的 CPU 降级必须由用户显式开启，其词级对齐与粗粒度说话人聚类是可选派生产物。任何速度或质量差异都需要在同一台机器和相同授权样本上实测。

### ADR-006：AI 明确选择，字幕与总结解耦

决定：生成偏好决定阶段集。没有选择 AI 笔记时 `notes_enabled=false`，不冻结笔记 profile，也不调用 LLM。

理由：降低费用和数据外发，符合用户对“只识别字幕”的直觉。

### ADR-007：参考项目采用以效果和边界为门禁

决定：DownKyi/vivo 中的实现或组件不因“开源”自动采用，也不因 GPL 自动排除。个人内部使用可做实验；进入 VtNote 主路径前必须以相同授权样本证明字幕成功率、耗时、失败恢复或资源占用的净收益，并保持当前网络和密钥边界。

当前可借鉴：独立内容选择、阶段进度、外部任务恢复、字幕语言轨枚举、结构化并发/取消和流式网络处理。B 站合集/列表已参考本地 DownKyi 快照中的 season/series 分页思路，以 VtNote 自有 typed adapter 和受控 HTTPS 传输重新实现；没有复制其 UI、下载器或持久化代码。

当前不引入 aria2/DownKyi FFmpeg 的原因不是许可证页面尚未完成，而是本次故障发生在 ASR 大小判断，且单音频下载尚无实测收益。若后续采用 aria2，必须只监听 loopback、使用随机 `rpc-secret`、保持证书校验且禁止任意 Origin；私有 B 站接口只能放在 typed adapter 后并保留 yt-dlp 后备。GPL 代码如被复制，必须记录来源并在任何对外分发前重新审查整个组合。详见 [参考项目](reference-projects.md#downkyi-161-本地快照审计)。

### ADR-008：发行许可从实际制品生成

决定：手写依赖表只作开发索引；正式发行必须基于 Python wheel、npm lock、Conda 包、模型和二进制生成 SBOM/NOTICE，并记录哈希与许可证正文。

许可展示页面不是当前功能优先级，可以延期；这不改变实际发生对外分发时的许可义务。

当前阻断项：仓库没有项目级 `LICENSE`；开发环境 FFmpeg 启用了 `--enable-gpl`。在决定发行许可并完成制品审计前，不把开发环境直接作为可分发包。

### ADR-009：兼容门面的渐进式模块化

决定：保持现有公开 Python 导入路径、HTTP 路径、数据库 schema 和任务快照合同，通过
合同模块、纯策略模块、路由注册器和能力服务逐步拆分历史大文件。禁止为了目录整齐进行
一次性全仓重命名，也不引入通用 repository/use-case 抽象来包装只有一个实现的代码。

理由：当前工作树包含已验证的平台、ASR、删除和恢复语义。渐进拆分能让回归测试覆盖每次
迁移，同时避免“分层”只增加转发类。只有存在独立变化原因、独立安全边界或第二个调用入口
时才新增模块；旧门面在所有调用者迁移并完成发布兼容评估前保留。

## 已知技术债

1. active cache 资产缺少统一保留期限与用户清理策略。
2. 未登记 yt-dlp partial 和模型安装 trash 缺少完整回收。
3. 前端 ASR/AI 连接页结构仍有重复；页面状态已开始迁入 `features/`，样式已按创建、
   内容库、详情和设置拆分，后续新增样式必须进入所属能力文件，不能重新堆回聚合入口。
4. 隐藏路由和兼容 provider 增加了文档/测试边界，需要产品决定保留或完整删除。
5. 仓库项目许可证、发行 SBOM 和安装包尚未冻结。
