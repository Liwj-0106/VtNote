# VtNote V1 技术决策记录

状态：架构基线
Owner：VtNote 技术负责人
下次评审：实现计划 Task 3C/4/5 各阶段开始前，及 POC 结论产生后
证据截止：2026-07-24
需求基线：[product-requirements.md](product-requirements.md)

## 1. 使用方法

本文记录 V1 已接受或暂定的技术决策。`Accepted` 表示实现应遵守，除非新增 ADR 明确
替代；`Provisional` 表示方向已选，但须由 POC 或外部合同复核后才能成为发布承诺。
“已接受”不代表代码已经完成，实际状态见 [traceability.md](traceability.md)。

每条 ADR 均记录背景、选项、决定、后果与重审触发器，避免把调研事实、产品需求和
当前实现混在一起。

## 2. 总体结构

```mermaid
flowchart TB
    UI["React/Vite 静态网站"] --> API["FastAPI API（同源）"]
    API --> DB["SQLite WAL：配置元数据、task/item/stage/lease"]
    API --> KEY["Windows Credential Manager / keyring"]
    WORKER["独立 durable worker"] --> DB
    WORKER --> SRC["显式 SourceAdapter：Bilibili / YouTube / local"]
    WORKER --> ASR["显式 Transcriber：Volc / faster-whisper"]
    WORKER --> AI["显式 Translator / NoteGenerator"]
    WORKER --> STORE["规范产物与运行资产"]
    API --> STORE
```

边界：

- API 负责校验、配置生命周期、任务快照、查询与变更命令，不执行长任务；
- worker 通过 SQLite 租约/心跳领取阶段，不依赖 API 进程内队列；
- 适配器把外部结果转换为 provider-neutral 类型；
- 规范产物通过原子文件操作发布，数据库只保存元数据/引用；
- UI 只消费同源 API，不直接调用平台或 AI 供应商。

## ADR-001：Windows 本机、同源、回环监听

状态：Accepted
关联：FR-001、NFR-001、NFR-002、NFR-005

### 背景

V1 面向单个 Windows 用户，需要访问本机媒体、Credential Manager、FFmpeg 和本地
模型。公网服务会引入账户、租户隔离、上传、授权与运维问题。

### 选项

1. FastAPI 监听所有网卡，React 独立开发服务器；
2. Electron/原生桌面壳；
3. FastAPI 只监听 `127.0.0.1:8765`，生产 React 静态构建由其同源提供。

### 决定

采用选项 3。开发期可有 Vite 开发流程，但发布路径必须同源、验证 `Host`/`Origin`，
不配置宽松 CORS。启动器/监督器负责 API 与 worker 生命周期。

### 后果

- 简化 CSRF/CORS 与部署，保留浏览器开发效率；
- 仍须防范本机恶意网页发起的请求，因此变更接口需要本地会话/CSRF；
- 不支持局域网、远程访问或多用户；此限制必须在文档和启动日志可见；
- 将来若需要桌面壳，应复用 API/worker 边界，不把业务逻辑迁入 UI。

### 重审触发器

正式需求出现局域网/公网、多用户、移动端或操作系统原生深度集成。

## ADR-002：SQLite WAL 持久队列与独立 worker

状态：Accepted
关联：FR-005、FR-014 至 FR-016、NFR-003、NFR-007

### 背景

下载、转码、ASR、翻译和笔记可持续数分钟到数小时，必须跨页面刷新和进程重启。V1
又不需要 Redis/Celery 的独立服务。

### 选项

1. API 内存队列/线程池；
2. Redis + Celery/RQ；
3. SQLite WAL + task/item/stage_run + lease/heartbeat 的独立 worker。

### 决定

采用选项 3。阶段最新 attempt 为调度单位；领取使用原子条件更新，租约包含 owner、
过期时间和心跳。启动恢复只重领已过期且满足幂等规则的尝试。

### 后果

- 安装和备份简单，状态可由数据库重建；
- SQLite 适合单机有限并发，不应扩展成多节点协调器；
- 每个阶段必须区分“可安全重做”“外部结果未知”“已发布不可覆盖”；
- 数据库事务不能与外部 HTTP/进程执行形成伪原子性，需显式幂等键和产物提交协议；
- worker 尚未在当前基线实现，不能以现有测试推断恢复能力已完成。

### 重审触发器

出现多机 worker、持续高写并发、远程队列、优先级/定时任务或 SQLite 锁竞争超出发布
门禁。

## ADR-003：不可变 `transcript.json` 是唯一规范源

状态：Accepted
关联：FR-011、FR-017、FR-019、NFR-003、NFR-009

### 背景

平台字幕、云 ASR、本地 ASR 的字段、时间精度与返回格式不同。若保存多个派生字幕为
事实源，重试与翻译很容易错配。

### 选项

1. 以 SRT 为主文件；
2. 把全部 cue 只存数据库；
3. 使用有 schema 版本、稳定 cue ID、毫秒时间和 provenance 的不可变 JSON。

### 决定

采用选项 3。每个成功 item 只发布一个规范源转录；确定性 UTF-8 序列化、原子
create-only 写入。SRT/VTT/TXT/Markdown 由规范 JSON 按需生成。译文保存源转录哈希
并严格对应 cue ID。

### 后果

- 输出可重现，翻译/笔记与来源解耦；
- 规范 schema 变化需要显式版本和迁移/兼容策略；
- 用户编辑能力不能直接覆盖规范源；V1 因此不提供内联编辑；
- 动态导出不得注入当前时间等非确定字段。

### 重审触发器

需要人工编辑、协作冲突、多轨/逐字级结构，或单文件大小影响读取性能。

## ADR-004：固定且可审计的字幕优先顺序

状态：Accepted
关联：FR-006、NFR-008、NFR-010

### 背景

字幕通常比 ASR 更快、更便宜，但轨道可能是自动生成、翻译、损坏或格式不支持。笼统
“取第一条字幕”无法复现选择。

### 决定

候选组固定为：

1. 首选语言人工字幕；
2. 首选语言自动字幕；
3. 其他语言人工字幕；
4. 其他语言自动字幕。

组内先按语言优先级，再按 `VTT > SRT > ASS > JSON`。逐条取得并严格校验；失败记录
安全警告并尝试下一条。所有候选均失败后，才允许取得一次音频。有效字幕路径的音频
下载与 ASR 调用必须为 0。

排序前排除 translated track 与 live chat；adapter 无法确定人工属性时保守映射为
自动。组、语言、格式均相同时，以稳定 track ID 作最终决胜，避免平台返回顺序导致
结果漂移。

### 后果

- 决策可测试、可解释，减少费用；
- 平台元数据不足时需明确“未知是否自动/翻译”，不得猜测；
- 平台字幕质量仍需人工复核，字幕优先不等于字幕绝对正确。

### 重审触发器

POC 表明某平台自动字幕显著优于人工/翻译轨，或新增可验证的用户手动选轨需求。

## ADR-005：平台能力位于显式适配器，yt-dlp 是易变依赖

状态：Accepted
关联：FR-003、FR-006、FR-007、NFR-009、NFR-010

### 背景

Bilibili 官方开放平台目录在审计日未提供面向任意公开视频的通用字幕读取合同
（[官方目录](https://openhome.bilibili.com/doc)）。yt-dlp 支持大量站点和字幕，但
其提取器追随网站变化；官方 README 也体现了平台能力和依赖的滚动变化
（[yt-dlp](https://github.com/yt-dlp/yt-dlp)）。

### 选项

1. 在业务层直接调用 yt-dlp；
2. 复制 BiliNote/yt-dlp 的平台代码；
3. 版本固定的 yt-dlp 放在 `SourceAdapter` 后，并用平台验收语料做合同测试。

### 决定

采用选项 3。Bilibili、YouTube、本地媒体/字幕是显式实现，不动态发现插件。适配器只
返回安全元数据、字幕描述或运行资产引用；URL 每次重定向都经过安全策略。V1 不传
Cookie，不读取浏览器登录态。

### 后果

- yt-dlp 更新可被隔离和回滚，但仍可能因平台变更失效；
- 版本升级必须先跑 30–50 视频 POC 中的平台子集；
- 错误口径必须区分 unsupported、auth/region、removed、temporary 和 adapter drift；
- 许可证清单需同时审计 yt-dlp 源码/轮子与发布二进制所带组件，不能只写
  “Unlicense”。

### 重审触发器

平台发布稳定官方读取接口、yt-dlp 许可/运行时要求变化，或连续适配故障无法满足门禁。

## ADR-006：V1 云 ASR 固定火山引擎极速版 Base64 路径

状态：Provisional（接口方向接受，质量/成本待 POC）
关联：FR-008、FR-009、FR-019、NFR-002

### 背景

需要一个中文友好、单请求返回的云端后备。火山引擎当前
[极速版文档](https://docs.volcengine.com/docs/6561/1631584?lang=zh)描述
`X-Api-Key`、`volc.bigasr.auc_turbo`、JSON Base64/URL 请求，以及 2 小时、100 MB
等当前限制。外部合同和价格易变。

### 决定

V1 只实现：

- `POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash`；
- `X-Api-Key`、唯一请求 ID、`X-Api-Resource-Id: volc.bigasr.auc_turbo`；
- 本地转码为 16 kHz、单声道、32 kbps OGG/Opus；
- JSON `audio.data` Base64 上传；
- 同一响应内映射 utterance/word 信息到 provider-neutral 中间结果。

V1 不实现 `audio.url`、云端对象存储、客户端/云端切片、轮询或其他火山模型。发送前
重新核验官方合同，并执行比 2 小时/100 MB 更保守的应用预检/建议阈值。

### 后果

- 单一协议降低实现与故障状态数量；
- Base64 有约 4/3 体积放大，必须在构造请求前检查内存和请求体；
- OGG/Opus 解码采样率细节需用 FFprobe/解码结果验证，不能只相信容器头；
- 供应商价格、区域、保留策略以用户当前控制台/合同为准；
- OpenAI、AssemblyAI 只作研究对照，不进入 V1 provider 列表。

### 重审触发器

POC 质量/成本不达阈值、官方接口/资源 ID/格式变化，或单请求内存和时长无法满足目标
语料。

## ADR-007：上传授权绑定修订；未知云结果不盲重试

状态：Accepted
关联：FR-002、FR-008 至 FR-010、FR-016、FR-019

### 背景

每任务重复同意会造成提示疲劳，但永久全局同意会在地址、模型或配置变化后失真。云端
请求超时还可能已被服务端接纳并计费。

### 决定

- 上传授权绑定当前 profile revision 与其 connection revision；
- 只有连接测试成功且授权修订都匹配时，`auto`/`cloud` 才能包含该云 profile；
- 没有有效云 profile 时，`auto` 仍可创建并把本地路由写入快照；只有 `cloud` 被阻止；
- 相关配置、地址、模型或连接修订变化时测试/授权按生命周期规则失效；
- 任务快照保存所用 profile/connection/授权修订，不保存密钥；
- 创建页持续显示供应商、路由、隐私和可能计费，并允许切 `local`；
- 明确未受理/明确失败时，`auto` 可转本地；
- 请求已发送但结果未知时记录 `cloud_outcome_unknown`，不得自动再次发云请求；可转
  本地并保留“可能已计费”警告；
- 用户若显式新建云 attempt，必须二次确认重复计费风险。

### 后果

- 在减少重复弹窗的同时让授权语义可证明；
- 配置生命周期、任务快照和 UI 必须使用同一修订判断；
- 不能把 HTTP 客户端普通 retry 中间件用于该 POST；
- 需要故障注入测试覆盖 DNS、连接前失败、写入中断、响应超时和明确 4xx/5xx。

### 重审触发器

供应商提供可查询的幂等请求结果、可用 idempotency key，或法规要求逐任务确认。

## ADR-008：本地 ASR 使用 faster-whisper 分段时间戳

状态：Accepted（模型/精度参数待 POC）
关联：FR-010、FR-011、NFR-005、NFR-008

### 背景

[faster-whisper](https://github.com/SYSTRAN/faster-whisper) 提供 CTranslate2 后端，适合
CPU/GPU 本地后备。WhisperX 提供强制对齐和说话人分离，但增加语言对齐模型、GPU/
Hugging Face 条件和更多故障面；其官方 README 也列出重叠说话和语言模型限制
（[WhisperX](https://github.com/m-bain/whisperX)）。

### 决定

V1 固定 `faster-whisper==1.2.1` 与 `ctranslate2==4.8.1`，懒加载模型，CPU 必须可用，
GPU 仅在探测通过时启用。规范 schema 以 segment 起止时间为门禁；若 provider 返回
word，可保留为可选 provenance/扩展，但 V1 UI 与导出不依赖逐字级时间。

WhisperX 不进入 V1；V1.1/Later 只有在 POC 证明逐字对齐的用户价值大于模型、显存、
许可证与运维成本后再评估。

### 后果

- 依赖较少、离线可运行、与当前实现 pin 一致；
- 分段边界不等同专业字幕切分，产品需诚实说明；
- 模型文件按需下载到 D 盘，校验来源/哈希/空间；不得隐式写 C 盘；
- 默认模型、compute type、VAD 参数必须由 POC 决定，本文不编造性能。

### 重审触发器

POC 时间戳门禁失败、用户明确需要逐字高亮/说话人，或 faster-whisper/CTranslate2 在
目标 Windows/CUDA 矩阵不可维护。

## ADR-009：翻译与笔记使用显式 OpenAI-compatible chat 适配器并行执行

状态：Accepted
关联：FR-012、FR-013、FR-016、NFR-009

### 背景

翻译和笔记都依赖转录，但彼此不应串联。将供应商 SDK/响应直接写进业务产物会导致
锁定，也会让一个可选功能失败拖累另一个。

### 决定

- 显式 `ChatConnection/Profile` 支持 OpenAI-compatible HTTP 合同，不动态加载插件；
- `Translator` 输入规范 cue，输出相同 cue ID 和源哈希的结构化译文；
- `NoteGenerator` 输入原始规范转录，支持摘要/关键点/自定义模板；
- `translate` 与 `notes` 都只依赖 `transcribe`，由 worker 独立领取；
- 结构化响应失败时，翻译只允许一次更小 batch 的受控重试；不得重跑 source/ASR；
- 长笔记采用有界 chunk/map/reduce，记录模板/模型/config revision；
- 可选分支失败使 item `completed_with_warnings`，不遮蔽原文。

### 后果

- 可更换兼容供应商，且阶段重试成本可控；
- “兼容”需通过 profile connection test 验证，不能假定所有端点完全相同；
- 提示词、响应、token/字符限制和错误必须脱敏并有界；
- 笔记默认开启规则依赖当前测试成功 profile，用户关闭后不得自行重新开启。

### 重审触发器

需要供应商特有结构化输出、离线翻译成为 V1.1，或 POC 表明 chat 翻译无法保持 cue
结构。

## ADR-010：密钥、持久数据与运行缓存三分离

状态：Accepted
关联：FR-002、FR-004、FR-018、FR-019、NFR-002、NFR-004、NFR-007

### 决定

- 代码：`D:\Workspace\Project\VtNote`；
- 持久数据：默认 `D:\Workspace\Project\VtNote-data`；
- 运行缓存：默认 `D:\Workspace\Codex\cache\VtNote-runtime`；
- 密钥：Windows Credential Manager，经 `keyring` 抽象；
- SQLite/API/log/任务快照只保存 credential reference/`has_secret`；
- 普通 task/item/result API 不返回本机绝对源路径或密钥；专用 setup/storage API
  可以返回应用管理根目录，用于解释存储与清理边界；
- 用户原件只读且永不由应用删除；
- 应用管理缓存先移入 24 小时可恢复回收区，move/purge 全量记录。

### 后果

- 备份、清理与威胁边界清楚；
- keyring 写入成功、数据库提交失败等跨系统事务需补偿/清理重试；
- 路径必须在每次写、移、清前重新 resolve 并验证根目录；
- 运行诊断包需要脱敏 URL、路径、header、密钥和自定义提示词。

### 重审触发器

便携版、Windows 账户漫游、可移动盘、加密数据目录或企业密钥管理需求。

## ADR-011：V1 不引入插件、Cookie 或浏览器助手

状态：Accepted
关联：FR-003、NFR-002、NFR-009、NFR-010

### 背景

BiliNote 的 Cookie/浏览器扩展和多 provider 工厂可扩展平台，但会扩大秘密读取、第三方
代码执行和登录内容合规面。V1 范围只有两个 URL 平台和本地入口。

### 决定

V1 所有适配器在代码中显式注册；不扫描插件目录、不执行用户提供模块、不读取浏览器
Cookie、不提供扩展。BiliNote 的相关模块只作风险/UX 参考，不复用。

V1.1 可重新评估“显式浏览器助手”，前提是主动安装、最小权限、可撤销、公开内容，
且不允许绕过登录/付费/地区限制。

### 后果

- 安全审计和支持矩阵更小；
- 对某些公开但反自动化的链接成功率较低，应提示本地文件后备；
- 新平台需代码变更、测试和发布，而不是运行时安装。

### 重审触发器

稳定的第三方适配需求超过显式实现成本，且完成独立权限/签名/沙箱设计。

## ADR-012：按需确定性导出，不持久化派生副本

状态：Accepted
关联：FR-017、FR-018、NFR-003、NFR-008

### 决定

原文/译文的 JSON、SRT、VTT、TXT、Markdown 在下载时从规范 JSON/译文 JSON 生成。
相同输入和格式应字节一致；不注入当前导出时间，不缓存无必要副本。格式不能表达某个
时间/文本时返回明确错误，不静默截断。

### 后果

- 降低磁盘占用和过期副本；
- 导出实现是纯函数，可用 golden tests 验证；
- 大文件导出要流式/限制内存，并设置安全文件名与 Content-Disposition。

### 重审触发器

导出成本成为交互瓶颈，或需要签名归档、用户编辑版和批量打包。

## 3. 跨 ADR 不变量

以下规则不得由单个适配器自行改变：

- 有效字幕路径不取得音频、不运行 ASR；
- `transcript.json` 已发布后不可覆盖；
- 可选分支失败不改变核心原文成功；
- 云端未知结果不自动重复远程副作用；
- 任务使用创建时配置/授权快照，不读取“最新默认项”改变历史；
- 密钥不进入数据库、API、日志或产物；
- 用户原件不修改、不删除；
- 所有长任务只在 worker 执行；
- 任何未测得的速度、准确率和成本都保持“待验证”。

## 4. 当前实现与决策差距

| 决策 | 基线 HEAD 状态 | 下一实现任务 |
|---|---|---|
| ADR-001 | API 的 Host/Origin/CSRF 基础已实现；静态 UI/启动器未实现 | Task 5/6 |
| ADR-002 | SQLite task/item/stage 模型与服务已实现；lease/heartbeat/worker 未实现 | Task 3C |
| ADR-003 | schema、不可变写入与按需导出已实现 | 后续只扩展调用链 |
| ADR-004 | 选择/逐条字幕校验原语已实现；真实平台调用未接通 | Task 3C |
| ADR-005 | 仅输入 URL/DNS 预检与 source protocol 基础已实现；逐连接 DNS pin、受控 redirect、yt-dlp 网络边界和平台 adapter 均未实现 | Task 3C |
| ADR-006 | FFmpeg 云音频原语部分已实现；火山 eligibility/HTTP/映射未实现 | Task 3C |
| ADR-007 | 测试/上传授权修订与任务快照已实现；worker 远程副作用状态未实现 | Task 3C |
| ADR-008 | 依赖已 pin；实际模型加载/转录未实现 | Task 3C |
| ADR-009 | 配置/schema/阶段依赖已实现；chat 调用与产物生成未实现 | Task 4 |
| ADR-010 | 路径、keyring、回收与清理原语大部已实现；监督/完整清理流程待集成 | Task 3C/6 |
| ADR-011 | 当前无插件/Cookie 实现，符合 | 持续安全门禁 |
| ADR-012 | 后端导出纯函数与 API 已实现；网站下载交互未实现 | Task 5 |

该表是 2026-07-24 的代码审计快照，不是路线图完成声明。

## 5. 发布前必须关闭的技术问题

1. 30–50 视频 POC 尚未运行：云/本地质量、RTF、成本和平台覆盖无结果；
2. 火山引擎请求体内存上限、应用更保守硬限制和超时值需以真实样本冻结；
3. faster-whisper 默认模型、compute type、CPU/GPU 支持矩阵需实测；
4. worker 租约时长、心跳、启动恢复和关机取消策略需故障注入；
5. Bilibili/YouTube adapter pin 升级与验收语料的维护责任需指定；
6. chat provider 的最大输入、结构化输出与一次小 batch 重试合同需测试；
7. Windows 启动器、静态深链、日志轮转、回收恢复和依赖许可证清单需完成；
8. 外部供应商接口、价格、区域和数据保留需在发布日重新核验
   [来源登记](research-sources.md)。
