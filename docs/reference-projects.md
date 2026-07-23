# VtNote V1 参考产品与复用审计

状态：已审计基线
Owner：VtNote 产品/技术负责人
证据截止：2026-07-24
完整来源：[research-sources.md](research-sources.md)

## 1. 结论先行

- VtNote 不是 BiliNote 的分支，也不复制其代码；本次任务没有复制任何第三方源码。
- 本地 BiliNote 快照适合参考入口、进度、历史、转录查看与设置的信息架构；其
  Cookie、浏览器扩展、明文 provider secret、进程内执行器和一体化 note pipeline
  不符合 VtNote V1 安全/持久性边界。
- 平台接入采用 VtNote 自有显式 adapter + 固定版本 yt-dlp，不直接移植 BiliNote
  downloader。
- 本地 ASR 采用 VtNote 自有 faster-whisper adapter；WhisperX、VideoLingo 和 Argos
  是后续候选/工程参考，不进入 V1 依赖。
- BibiGPT、Videosays、AssemblyAI、OpenAI 和火山引擎用于产品/服务对照，不代表 V1
  集成承诺。

## 2. 分类与评判标准

### 2.1 参考产品/服务

用于回答“用户能完成什么、如何呈现、商业/隐私边界如何表达”：

- BiliNote；
- BibiGPT；
- Videosays；
- VideoLingo（既是开源项目，也体现完整产品工作流）；
- AssemblyAI、OpenAI、火山引擎（ASR 服务对照）。

### 2.2 可复用组件候选

用于回答“某个技术能力能否作为依赖或适配器实现”：

- yt-dlp；
- faster-whisper / CTranslate2；
- WhisperX；
- Argos Translate。

### 2.3 决策标签

| 标签 | 定义 |
|---|---|
| 直接复用 | 按原包/源码使用，保留许可证并接受其接口；本次 BiliNote 审计没有此类项 |
| 适配重写 | 只借鉴职责/合同，在 VtNote 边界内自行实现；不复制源码 |
| 仅参考 | 只吸收 UX、风险或流程观察，不进入生产实现 |
| 拒绝 | 与 V1 安全、架构或范围冲突，不采用 |

## 3. BiliNote 本地快照审计

### 3.1 身份与证据边界

审计目录为 `D:\Workspace\Project\BiliNote-master`。其本地 README 标题自报
`BiliNote v2.4.4`，但目录内没有 `.git`，因此无法证明 commit SHA、打包日期，或与
任何上游 release 的一致性。上游项目身份与 MIT 许可证分别见
[仓库](https://github.com/JefferyHcool/BiliNote)和
[LICENSE](https://github.com/JefferyHcool/BiliNote/blob/master/LICENSE)；本文不声称
该本地 archive 就是上游某个 commit，也不记录不可靠的“最新版本”。

### 3.2 模块观察

| 区域 | 本地证据 | 观察 |
|---|---|---|
| 下载器合同 | `backend/app/downloaders/base.py` | 把来源取得抽成基类，方向可借鉴 |
| YouTube | `youtube_downloader.py`, `youtube_subtitle.py` | 平台媒体与字幕职责分开 |
| Bilibili | `bilibili_downloader.py`, `bilibili_subtitle.py` | 依赖站点行为，并有 Cookie/登录相关路径 |
| 本地输入 | `local_downloader.py` | 本地媒体进入同一处理流 |
| 转录器 | `backend/app/transcriber/base.py`, `transcriber_provider.py` | provider factory + 多转录实现 |
| 本地 Whisper | `backend/app/transcriber/whisper.py`, `whisper_models.py` | faster-whisper 模型选择与调用参考 |
| 主流水线 | `backend/app/services/note.py` | 下载、转录、截图、笔记与文件缓存集中编排 |
| 执行器 | `task_serial_executor.py` | 进程内执行，不提供 VtNote 所需的 DB lease/heartbeat |
| 配置/密钥 | `provider.py`, `db/models/providers.py` | provider 数据落 SQLite；显示掩码不等于安全存储 |
| Cookie | `cookie_manager.py` | 配置 JSON/临时文件参与 Cookie 流程 |
| 服务/API | `backend/main.py` | 静态文件、uploads、CORS 等服务设置与 VtNote 边界不同 |
| 数据 | `db/engine.py`, `sqlite_client.py` | 使用 SQLite，但任务/产物语义不同 |
| RAG | `services/vector_store.py` | Chroma/问答超出 V1 |
| 创建/历史 | `BillNote_frontend/src/pages/HomePage/` | NoteForm、History、StepBar 等 UX 参考 |
| 结果 | `NoteHistory`, `transcriptViewer`, `ChatPanel` | 转录、笔记、聊天的内容组织参考 |
| 首次启动/设置 | `pages/Onboarding/`, `pages/SettingPage/` | 配置向导、模型/下载器/转录/提示词设置参考 |
| 浏览器扩展 | `BillNote_extension/` | Cookie/页面辅助能力扩大权限面 |

### 3.3 模块级复用矩阵

| BiliNote 模块/模式 | 标签 | VtNote 决定 | 原因/约束 |
|---|---|---|---|
| `downloaders/base.py` 的职责拆分 | 适配重写 | 保留 `probe / fetch_subtitle / fetch_audio` 概念，自行定义 typed protocol | VtNote 需 URL 安全、不可变产物和 DI 合同 |
| YouTube downloader/subtitle | 适配重写 | 通过固定版本 yt-dlp 的 VtNote adapter 实现 | 不复制平台代码，统一错误与安全策略 |
| Bilibili downloader/subtitle | 仅参考 | 用 yt-dlp adapter + 平台语料合同测试 | BiliNote 路径依赖网站接口/Cookie，非官方稳定合同 |
| `local_downloader.py` | 适配重写 | 浏览器上传与受信本机路径分开，原件只读 | VtNote 已有自己的安全上传/本地校验 |
| transcriber base/factory | 适配重写 | 显式 `Transcriber`，编译期注册 Volc/local 两项 | 不采用动态 provider/plugin 工厂 |
| `whisper.py` / model UI | 仅参考 | 自有 lazy faster-whisper adapter 与 D 盘模型目录 | schema、取消、provenance、资源门禁不同 |
| `services/note.py` 一体流水线 | 拒绝 | source/transcribe/translate/notes 分阶段 | 单体流程不利于阶段重试、平行可选分支和恢复 |
| `task_serial_executor.py` | 拒绝 | SQLite lease/heartbeat 独立 worker | 进程内状态无法满足重启恢复 |
| provider UI 掩码 | 仅参考 | 可以参考“已配置/未配置”显示 | 存储必须改为 Windows Credential Manager |
| provider secret SQLite 模型 | 拒绝 | DB 只保存 credential reference/`has_secret` | 掩码不保护静态数据库 |
| Cookie manager | 拒绝 | V1 不保存/读取 Cookie | 登录/会员/权限和泄漏风险超范围 |
| 浏览器扩展 | 拒绝 | V1 不提供；V1.1 仅可重新评估显式助手 | 不得静默读取登录态或绕过限制 |
| 文件状态 JSON/缓存布局 | 拒绝 | task/item/stage 进 SQLite，产物走 typed paths | 避免文件状态与数据库漂移 |
| Home/NoteForm/History | 仅参考 | 吸收“创建 + 最近任务 + 下一步”信息层级 | React 状态/API/安全合同需重写 |
| StepBar/任务进度 | 仅参考 | 吸收阶段可见与失败恢复概念 | VtNote 阶段、attempt、警告语义更严格 |
| transcriptViewer | 仅参考 | 吸收时间戳文本阅读结构 | V1 只读规范转录，不复制组件 |
| Onboarding/Settings | 仅参考 | 吸收渐进配置与连接状态呈现 | VtNote 需测试修订、上传授权和 keyring |
| ChatPanel/vector store | 拒绝 | V1 无 RAG/聊天 | 与“紧凑文本资产”范围不符 |

### 3.4 可借鉴、不应照搬

可借鉴：

- 从单一入口创建任务并持续显示阶段；
- 历史记录与结果回访；
- 模型/供应商设置的渐进披露；
- 转录与 Markdown 笔记并列阅读；
- 对本地模型下载/环境问题给新手说明。

不应照搬：

- 把密钥和 Cookie 放入 SQLite/JSON；
- 把长任务放在 API 进程内；
- 把下载、ASR、笔记和截图绑成一个重试单元；
- 允许浏览器扩展或 Cookie 成为公开 URL 的默认成功条件；
- 暴露 uploads/本机路径或使用宽松 CORS；
- 为追求功能数量加入 RAG、截图、多模态或配音。

## 4. 产品/商业服务对照

### 4.1 能力对照

| 产品/服务 | 官方当前能力证据 | 对 VtNote 的启发 | V1 决定 |
|---|---|---|---|
| BiliNote | 上游仓库描述多平台链接、本地视频、Whisper、AI 笔记等（[SRC-001](https://github.com/JefferyHcool/BiliNote)） | 本地部署 + 多阶段结果对用户有价值 | 只作 UX/架构反例审计 |
| BibiGPT | 官方文档列出多平台与本地文件；产品强调转录、摘要、思维导图/聊天（[平台文档](https://docs.bibigpt.co/getting-started/bibigpt-supported-platforms)、[产品页](https://bibigpt.co/)） | 用户期待“一次提交后直接获得可用知识产物” | V1 只做转录、可选翻译/笔记，不做导图/RAG |
| Videosays | 官方页支持公开链接、时间戳、TXT/SRT/VTT，并说明私有/登录/地区内容可能不可访问（[官网](https://videosays.com/)） | 输入边界和人类复核提示应直白 | 作为公开链接 UX/错误口径参考 |
| VideoLingo | 开源流程覆盖字幕切分、翻译、对齐和配音（[仓库](https://github.com/Huanshere/VideoLingo)） | 展示了高级本地化链路的价值与复杂度 | 仅参考；对齐/配音是 Later |
| 火山引擎 ASR | 极速版为单请求录音文件识别，当前合同见[官方文档](https://docs.volcengine.com/docs/6561/1631584?lang=zh) | 中文云端 ASR 可作为本地后备前的加速路径 | V1 唯一云 ASR，质量/成本待 POC |
| OpenAI Audio | 官方 reference 按模型限制响应格式与时间戳能力（[API reference](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)） | provider 能力必须按具体模型测试，不能按 endpoint 联集猜测 | 研究对照，不进入 V1 ASR |
| AssemblyAI | 官方提供 async/stream/sync 与商业 self-hosted（[价格](https://www.assemblyai.com/pricing/)、[自托管](https://www.assemblyai.com/deployments/self-hosted)） | 云端、自托管和开源本地是不同采购/部署类别 | 研究对照，不进入 V1 |

### 4.2 价格与版本说明

- Videosays 历史报告中的 RMB tier 已失效；当前官网只作为“5 分钟试用、之后按量购买、
  无订阅”的访问日快照，不记录不可验证的旧价。
- AssemblyAI 价格页在 2026-07-24 显示 Universal-3.5 Pro async USD 0.21/小时、
  Universal-2 USD 0.15/小时；这只用于量级对照，税费、附加能力、区域与合同另计，
  发布/采购前必须重查。
- 火山引擎价格、额度和促销以用户当前中国区控制台/合同为准；本文不把营销价写入
  V1 成本承诺。
- OpenAI 的输入/输出格式、模型和数据控制按 endpoint/account 变化；不使用历史报告
  的统一格式或统一保留期描述。

## 5. 开源组件对照

| 组件 | 能力 | 已核验版本/状态 | 许可证 | V1 决定 |
|---|---|---|---|---|
| yt-dlp | 平台媒体/字幕提取（[仓库](https://github.com/yt-dlp/yt-dlp)） | VtNote pin `2026.7.4` | 源码/轮子核心 Unlicense；发布二进制及依赖有额外条款 | **直接依赖**，放 adapter 后 |
| faster-whisper | CTranslate2 本地 ASR、CPU/GPU（[仓库](https://github.com/SYSTRAN/faster-whisper)） | VtNote pin `1.2.1` | MIT（[LICENSE](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)） | **直接依赖**，自有 adapter |
| CTranslate2 | faster-whisper 推理后端 | VtNote pin `4.8.1` | 分发前按锁文件与官方 license 复核 | faster-whisper 的固定依赖 |
| WhisperX | 强制对齐、word timestamps、可选 diarization（[仓库](https://github.com/m-bain/whisperX)） | PyPI 稳定版访问日显示 `3.8.6`；`3.8.7rc1` 为预发布 | BSD-2-Clause（[LICENSE](https://github.com/m-bain/whisperX/blob/main/LICENSE)） | V1 不依赖；Later POC |
| Argos Translate | 离线翻译与语言包（[PyPI](https://pypi.org/project/argostranslate/)） | 访问日显示 `1.11.0` | MIT 或 CC0 双许可 | V1.1 中英包候选 |
| VideoLingo | 完整字幕翻译/对齐/配音应用 | 不固定“最新”；参考访问日 default branch | Apache-2.0（[仓库说明](https://github.com/Huanshere/VideoLingo)） | 仅参考，不作组件依赖 |

“访问日版本”不是自动升级建议。每次依赖升级都应更新 lock、许可证清单并运行对应合同
测试，平台适配器还需跑 POC 子集。

## 6. 许可证与分发义务

### 6.1 BiliNote

BiliNote 上游是 MIT。若未来复制其源码的全部或实质部分，必须在副本中保留 copyright
和 permission notice；还应在 THIRD_PARTY_NOTICES 标出来源、commit 与改动。当前
没有复制代码，所以不产生该复制链路；“看过代码并重写职责”仍应在设计审计中保留
本文件记录。

### 6.2 yt-dlp

yt-dlp 仓库/源码分发标为 Unlicense，但其 README 明确提示不同发布文件可能打包 MIT、
ISC 或 GPLv3+ 等组件（[licensing 说明](https://github.com/yt-dlp/yt-dlp/blob/master/README.md)）。
VtNote 当前使用 Python package pin，不应把某个 PyInstaller 二进制的许可证结论套到
wheel；发布构建必须以实际 artifact 生成 SBOM/NOTICE。

### 6.3 faster-whisper、WhisperX、Argos、VideoLingo

- faster-whisper MIT：分发源码/实质部分保留 notice；
- WhisperX BSD-2-Clause：源码/二进制分发按条款保留 notice/disclaimer；
- Argos Translate 可在 MIT 或 CC0 路径下使用，但语言模型包可能有独立元数据/条款，
  每个下载包另验；
- VideoLingo Apache-2.0：若复制代码需处理 NOTICE、license 与修改标识；V1 不复制。

许可证审计不是法律意见；发布门禁以实际锁文件、wheel、模型和二进制内容为准。

## 7. 采用清单

### V1 采用

- yt-dlp 固定版本 + VtNote 显式平台 adapter；
- faster-whisper/CTranslate2 固定版本 + VtNote 本地 ASR adapter；
- BiliNote 的创建/进度/历史/设置仅作 UX 参考；
- VideoLingo 的阶段拆解仅作 Later 参考；
- 火山极速版作为唯一云 ASR，等待 POC 门禁。

### V1.1 评估

- Argos 中英离线语言包，按需下载到 D 盘并单独校验；
- 显式浏览器助手，只面向公开内容、最小权限、主动安装、可撤销；
- WhisperX 只有在逐字时间戳价值被 POC 证明后再进入候选。

### 拒绝/推迟

- BiliNote Cookie/浏览器扩展与 provider secret 存储；
- BiliNote 进程内 serial executor 和 monolithic note pipeline；
- 动态插件系统；
- AssemblyAI/OpenAI 作为 V1 ASR provider；
- RAG、聊天、配音、烧录、说话人分离、硬字幕 OCR；
- 任何登录、会员、DRM、地区限制绕过。
