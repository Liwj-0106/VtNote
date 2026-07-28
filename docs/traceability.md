# VtNote V1 需求追踪矩阵

状态：实现基线审计 + 剩余计划映射
Owner：VtNote 产品/QA 负责人
实现基线：`2a36eb0ac0419caf1a9b297c90bfb1e7d0baf8d7`
文档基线：`e4a8fdfe137c04d19e1ac9cd6dfd44520b73d5eb`
证据截止：2026-07-28
需求来源：[product-requirements.md](product-requirements.md)
历史实现计划：[2026-07-18-vtnote-v1.md](superpowers/plans/2026-07-18-vtnote-v1.md)
剩余开发计划：[2026-07-28-vtnote-v1-completion.md](superpowers/plans/2026-07-28-vtnote-v1-completion.md)

## 1. 状态定义

| 状态 | 含义 |
|---|---|
| 已实现（当前范围） | 当前代码已完成该行描述的后端/基础范围，并有直接测试；不自动代表网站或发布完成 |
| 部分实现 | 合同、模型或原语存在，但真实 adapter、worker、UI 或端到端链路仍缺失 |
| 未实现 | 当前代码没有执行该需求的生产路径 |
| 持续门禁 | 不是一次性功能；每次发布都必须复核 |

“有测试文件”不等于需求完成。现有测试只证明其直接覆盖的行为；外部服务均为注入/
mock 合同，当前没有 live credential 或 30–50 视频 POC 结果。

## 2. 功能需求追踪

| ID | 需求摘要 | 计划任务 | 当前状态 | 当前代码/测试证据 | 完成所需验证 |
|---|---|---|---|---|---|
| FR-001 | 首次启动与就绪检查 | Task 2, 5, 6 | 部分实现 | storage/env 配置与 `tests/test_environment_contract.py`, `test_storage.py`；当前环境缺 `yt-dlp-ejs`/Deno；无 setup UI/launcher | FFmpeg/目录/模型及 yt-dlp/EJS/Deno 三态 UI；YouTube 缺链时局部禁用；启动器仅回环 |
| FR-002 | 连接、profile、默认项、修订快照 | 历史 Task 2,4,5；剩余 Tasks 8,9,12,18 | 部分实现 | `configuration.py`；`test_configuration.py`, `test_configuration_lifecycle.py`, `test_api.py` 覆盖 keyring、测试/授权修订、归档、默认项 | 区分静态策略校验/付费能力测试/音频与文本授权；腾讯 ASR/COS 与百炼真实 tester；隔离旧 Volc/任意 chat 配置；设置 UI、修订失效和 secret DOM/network E2E |
| FR-003 | URL 校验与安全探测 | Task 2, 3, 6 | 部分实现 | `url_security.py`; `test_url_security.py`; API 注入探测和 redirect 报告重校验测试；一次 direct-only YouTube 超时仅为环境观察；现有 C 盘 Node 不作发行依赖 | `trust_env=false` transport、逐连接 DNS pin、受控 redirect；固定 yt-dlp/EJS/Deno D 盘链、哈希、禁远程 component；中国网络 direct-only corpus/本地后备；代理另立 ADR |
| FR-004 | 浏览器上传与受信本机文件 | Task 3, 5, 6 | 部分实现 | `uploads.py`; `test_uploads.py` 覆盖流式大小限制、sniff、失败回收、原件只读、UNC 拒绝 | 网站文件选择/上传取消；跨进程/崩溃 E2E；普通 task/item API 不泄露来源绝对路径；setup/storage 只返回应用根 |
| FR-005 | 持久 task/item/stage、快照和历史 | Task 2, 3, 5 | 部分实现 | `tasks.py`, `models.py`; `test_tasks.py::test_enqueue_creates_durable_rows_and_immutable_redacted_snapshot`; API list/get | lease worker 与重启恢复；创建禁重/成功跳转；历史和详情 UI |
| FR-006 | 固定字幕优先顺序 | Task 1, 3, 7 | 部分实现 | `sources.py`; `test_source_contracts.py` 覆盖人工/自动、语言/格式顺序和音频只取一次 | 真实 Bilibili/YouTube adapter；有字幕 corpus 的音频/ASR 调用数为 0 |
| FR-007 | 音频取得与 FFmpeg 预处理 | Task 3, 6 | 部分实现 | `media.py`; `test_media.py` 覆盖 shell-free、超时、真实 FFmpeg、部分文件隔离、原子发布；开发机 7.1.1 buildconf 已只读记录 | 平台 audio handoff；worker 取消/重试；资源/磁盘故障 E2E；实际发行 FFmpeg build/SBOM/NOTICE 审计 |
| FR-008 | ASR 路由、修订授权、费用提示 | Task 2, 3, 5 | 部分实现 | `configuration.py`, `tasks.py`; `test_profiles_enforce_protocol_and_upload_consent_revision`, `test_auto_mode_omits_cloud_profile_until_upload_is_authorized` 证明无授权时 auto 快照不含云 profile | 创建页显示 auto 落本地且仍可提交、仅 cloud 阻塞；worker 按快照路由；未授权网络调用为 0 |
| FR-009 | 腾讯录音文件识别与私有 COS 中转 | 历史 Task 3,7；剩余 Tasks 8,9,11,22 | 部分实现 | 已有云音频预处理原语和不可变规范转录；现有火山固定资源测试仅是被本轮方案取代的历史基线，不构成腾讯实现 | `CreateRecTask`/`DescribeTaskStatus` TC3 合同；≤4,500,000 B 走 Base64，较大合格音频走广州私有 COS；默认 `16k_zh_en_2.0`；固定错误表；TaskId/到期查询持久调度；provider 终态立即删，cancel/unknown 到 URL 过期+宽限清理；live POC |
| FR-010 | faster-whisper 与云转本地 | Task 3, 7 | 部分实现 | 依赖精确 pin；配置/测试已有 `large-v3-turbo/int8_float16/VAD` 与 D 盘根；当前 `device:auto` 尚未落实 GPU 必需；无生产 transcribe 调用 | segment、GPU concurrency=1、固定模型/CUDA、禁止静默 CPU、取消/provenance、明确失败 fallback、unknown 不重发 |
| FR-011 | 不可变规范转录 | Task 1 | 已实现（当前范围） | `schemas.py`, `artifacts.py`; `test_schemas.py`, `test_storage.py::test_transcript_write_is_immutable...`, export tests | 在真实字幕/云/本地三条链路做 E2E；schema 兼容与产物唯一性门禁 |
| FR-012 | 可选 cue 对齐翻译 | 历史 Task 1,2,4,5；剩余 Tasks 12,13,15,19 | 部分实现 | translation schema/hash、配置快照、平行 stage、按需译文导出已有测试 | 百炼北京 canonical workspace adapter；能力测试与文本授权；每次非流式 JSON；cue 数+UTF-8 大小分批、精确 cue 集、结构错误/unknown 付费隔离、UI、真实 provider contract |
| FR-013 | 可选 AI 笔记 | 历史 Task 2,4,5；剩余 Tasks 12,14,15,20 | 部分实现 | 内部 `summary/key_points/custom`、`zh-Hans`、profile 后一次性自动启用、parallel stage 与 note typed path 已有测试 | 百炼每层非流式 JSON；直接读原文、顺序 chunk/map/reduce、citation lineage、AI/task/hash/model 标识和复核提醒、UI/质量 POC |
| FR-014 | 进度、决策、错误与警告 | Task 2, 3, 5, 6 | 部分实现 | pipeline 状态聚合、stage attempts、warning/diagnostic sanitize 与 API | worker heartbeat/progress；全部错误码映射；详情 UI、轮询退避、重启恢复 E2E |
| FR-015 | 排队/运行取消 | Task 2, 3, 5 | 部分实现 | 现有测试覆盖 queued→canceled、running→cancel_requested 与 cancel_requested 重复请求；当前 HEAD 对 canceled 后重复请求仍拒绝，尚未达到目标合同 | 补“因取消已 canceled 时返回当前资源、其他终态拒绝”测试/实现；worker 安全检查点、部分资产、UI |
| FR-016 | 阶段重试与冲突 | Task 2, 3, 4, 5 | 部分实现 | `test_retry_is_stage_only...`, active/dependent conflict、parallel branch、duplicate race 测试 | worker 真执行；上游产物失效；`submission_unknown` 二次计费保护和显式确认重试；UI |
| FR-017 | 查看与确定性导出 | Task 1, 2, 5 | 部分实现 | `exports.py`; `test_exports.py`; `test_original_and_translation_exports_are_generated_on_demand`; API export | transcript/translation/notes viewer；下载菜单/文件名；长文性能和格式错误 UI |
| FR-018 | D 盘存储、回收、恢复与日志 | Task 1, 3, 5, 6 | 部分实现 | typed paths；`test_runtime_assets.py` 覆盖 trash/restore/24h purge/reparse/并发；upload/media cleanup | worker/launcher 接入；设置页占用/回收；计划清理与恢复 E2E；用户原件扫描 |
| FR-019 | 执行摘要与 provenance | Task 1, 2, 3, 4, 5 | 部分实现 | provenance schema、pipeline snapshot、stage attempts、sanitized public view 与基础确定性 Markdown 导出；现有 Markdown 未包含完整来源证明 | adapter/model/request/fallback/authorization 全链写入；腾讯 TaskId 仅作为安全内部恢复证据、COS key/签名 URL 与 raw cloud response 不落日志/产物；JSON/Markdown 完整安全来源证明；详情呈现；无遥测 |

## 3. 非功能需求追踪

| ID | 需求摘要 | 计划任务 | 当前状态 | 当前代码/测试证据 | 发布门禁 |
|---|---|---|---|---|---|
| NFR-001 | 回环、同源、Host/Origin/CSRF | Task 2, 5, 6 | 部分实现 | `test_exact_host_origin_and_double_submit_csrf_are_enforced`; bind 设置拒绝非回环；docs 默认关闭 | 生产启动器只监听 `127.0.0.1:8765`；静态同源；无宽松 CORS；浏览器 E2E |
| NFR-002 | keyring、密钥零泄漏、上传隐私 | Task 2, 3, 5, 6, 7 | 部分实现 | keyring 抽象/补偿；API、task、diagnostic redaction；configuration lifecycle tests | worker/cloud/log/UI/诊断包 secret scan；未授权云网络调用 0；COS bucket 私有、对象名不含来源信息、签名 URL 不持久化、provider 终态立即删除、unknown/cancel 延期安全清理与一日生命周期兜底；外部合同复核 |
| NFR-003 | durable worker、重启恢复、幂等 | Task 2, 3, 6, 7 | 部分实现 | SQLite task/stage 与 runtime asset crash-shape 原语；没有 lease worker | kill -9/断电形状故障注入；lease/heartbeat/重领；`next_poll_at` 到期单次查询且不占主 worker；TaskId 只查询；`submission_unknown` 不重提；取消后独立 COS 协调清理；无丢任务 |
| NFR-004 | 路径、命令、原件与回收安全 | Task 1, 3, 6, 7 | 部分实现 | typed paths、reparse/junction 防护、shell-free runner、原件只读、recoverable trash 测试 | 全链安全回归、launcher/cleanup、恶意文件名/路径 corpus、原件哈希不变 |
| NFR-005 | Windows/Python/GPU 兼容 | Task 3, 6, 7 | 部分实现 | Python 范围与精确依赖 pin；环境记录 native runtime；真实 FFmpeg 测试 | Win10/11 x64、批准默认与 GPU/CUDA 固定矩阵；安装/首次模型下载 E2E；CPU 仅诊断/未来备选 |
| NFR-006 | 键盘、AA、缩放、减弱动画 | Task 5, 7 | 未实现 | 当前无 React UI | 键盘核心旅程、焦点、屏幕阅读器、axe/人工 AA、200% 与 4 视口 |
| NFR-007 | 结构化脱敏日志与诊断包 | Task 2, 3, 6, 7 | 部分实现 | `sanitize_diagnostic` 写入边界与统一 API error shape 测试 | worker/launcher 结构化日志、轮转、关联 ID、禁止 COS key/签名 URL/raw cloud response、诊断包与全局 secret/path scan |
| NFR-008 | worker 性能与诚实进度 | Task 3, 5, 7 | 未实现（仅合同） | 长任务未执行；UI 未实现；media runner 有界 | POC RTF/内存/磁盘/轮询；无假百分比；API 交互基准 |
| NFR-009 | 显式 adapter、版本化、测试先行 | Task 1–7 | 部分实现 | typed protocols、schema、迁移、238 项基线测试；真实 adapter/orchestration/UI 未完成 | 全套单元/合同/集成/E2E/安全；依赖锁与迁移复核；review 关闭 |
| NFR-010 | 条款、版权、依赖许可与无绕过 | Task 3, 5, 6, 7 | 持续门禁 | URL allowlist、无 Cookie/plugin、参考项目许可审计；yt-dlp/EJS/Deno 许可已登记；当前 FFmpeg GPL v3+ 开发 build 已记录 | UI 权利提示；实际发行 FFmpeg、yt-dlp/EJS/Deno、其他依赖/模型 artifact 的 hash/buildconf、源码、SBOM/NOTICE；平台/供应商条款复核 |

## 4. 剩余计划任务到验收包

第 2、3 节的 `Task 1–7` 指历史计划；以下 `Task 1–25` 指
`2026-07-28-vtnote-v1-completion.md`。

| 剩余任务 | 交付边界 | 当前基线 | 完成门禁 |
|---|---|---|---|
| Tasks 1–2 | 生命周期证据模型、durable worker/lease/recovery | 模型字段部分存在；无真实 worker | owner/attempt/lease 防旧写、取消幂等、故障恢复与 GPU lease |
| Tasks 3–7 | source domain、固定网络边界、yt-dlp/EJS/Deno、双平台、字幕优先编排 | 只有纯合同、URL 预检和上传/media 原语 | hostile proxy/DNS/redirect 安全；YouTube 局部就绪；字幕路径零音频/ASR |
| Tasks 8–11 | 腾讯 ASR/COS 原子凭据包、提交协调、faster-whisper、ASR 路由 | 只有配置快照和 FFmpeg 预处理 | 旧 provider 隔离、TC3/COS 固定错误合同、到期单次查询、TaskId 恢复、unknown/cancel 安全清理、GPU-only fallback、单一规范发布 |
| Tasks 12–15 | 百炼 chat、数据授权、翻译、引用笔记、可选分支编排 | 只有配置/产物骨架 | 旧任意 endpoint 隔离、官方 workspace 构造、每层 JSON、cue/字节限制、citation lineage、付费 unknown 重试确认、不重跑上游 |
| Task 16 | readiness、storage、结果和完整任务 read models | API 基础存在；数据不足以支撑 UI | 无密钥/绝对路径；进度/证据/结果/分页；THR-004 基准 |
| Tasks 17–20 | React 基础、设置、创建、历史/详情 | 未实现 | CSRF/不重放、轮询、键盘/焦点/AA/缩放、静态 build、secret scan |
| Tasks 21–23 | supervisor、静态托管、日志/维护、依赖/发行证据 | 未实现 | 回环/无孤儿进程、深链/API 404、24h 回收、SBOM/NOTICE/buildconf |
| Task 24 | 离线 E2E、故障注入、安全和发布审查 | 未实现 | 全链 fixture、crash/unknown、副作用唯一性、浏览器 E2E、全需求证据 |
| Task 25 | 授权 live POC 与发布冻结 | 未运行 | 30–50 合法样本、真实账户/计费同意、原始度量、阈值和发行签字 |

## 5. 用户旅程验收

| 旅程 | 需求 ID | 最小 E2E |
|---|---|---|
| URL 有人工首选语言字幕 | FR-003, FR-005, FR-006, FR-011, FR-017 | 探测 → 选轨 → 规范转录 → 导出；断言音频/ASR 0 调用 |
| URL 人工字幕坏、自动字幕可用 | FR-006, FR-014, FR-019 | 下一候选成功；警告与最终选择可见 |
| YouTube EJS/Deno 缺失 | FR-001, FR-003, NFR-010 | 只禁用 YouTube URL；不调用系统 Node/远程 component；本地/Bilibili 路径继续 |
| 中国网络 direct-only URL 超时 | FR-003, FR-004, NFR-010 | 不继承代理、不外推平台结论；错误带环境限定；合法本地文件后备可继续 |
| URL 无字幕、小音频、授权有效 | FR-007–FR-011, FR-019 | OGG/Opus ≤4,500,000 B → Base64 `CreateRecTask` → 查询 → 单一规范转录；COS 调用 0 |
| URL 无字幕、大音频、授权有效 | FR-007–FR-011, FR-018, FR-019 | 广州临时私有 COS 对象 → 6 h 签名 URL → `CreateRecTask` → 到期单次查询 → provider 终态删除；签名 URL 不落库/日志 |
| 腾讯云预检不合格或 COS 未配置 | FR-008–FR-010, FR-014 | 时长/编码后大小/语言/配置在发送前判定；auto 本地、cloud 阻止；ASR 提交调用 0 |
| 腾讯明确失败、auto 转本地 | FR-008–FR-010, FR-014 | 未受理网络错误/限流/服务端错误按分类本地成功、fallback provenance；鉴权/模型/COS 配置错停止并提示修复 |
| 已持久化 TaskId 后 worker 崩溃 | FR-009, FR-014, NFR-003 | 重领后只调用 `DescribeTaskStatus`，不重复 `CreateRecTask`；24 h 结果窗口内恢复 |
| 提交结果未知 | FR-009, FR-010, FR-016, FR-019 | 标记 `submission_unknown`，绝不自动重提付费请求；允许本地 fallback；只有带可能重复计费确认的显式云重试 |
| 本地取消或提交结果未知后的 COS | FR-009, FR-015, FR-018, NFR-003 | 不按本地终态提前删除；独立协调器到 provider 终态或 URL 过期+30 分钟清理，不占主 worker |
| COS 清理失败 | FR-009, FR-018, NFR-002 | 记录脱敏清理事件并重试；bucket 一日生命周期仅作按日扫描兜底；不删除用户原件 |
| 本地媒体仅本地 | FR-004, FR-007, FR-010, FR-018 | 原件哈希不变、受控资产、本地转录 |
| 本地字幕 | FR-004, FR-006, FR-011 | 无 ASR、解析/规范化/导出 |
| 翻译失败、笔记成功 | FR-012–FR-014, FR-016 | 原文/笔记可用，item 为 warning，仅译文可重试 |
| 长字幕生成笔记 | FR-013, FR-017 | 原文 cue 顺序分块/合并；每个时间引用点击后定位同一规范 cue |
| 百炼地址或输出不合规 | FR-002, FR-012–FR-014 | 拒绝非北京官方工作空间 endpoint；连通测试要求包含 `JSON` 的最小非流式 `json_object` 响应；非法结构不污染产物 |
| 百炼请求结果未知 | FR-012–FR-016 | 500/503/读取中断不自动重发；原文可用；显式 AI 阶段重试要求可能重复计费确认 |
| 旧 Volc/任意 chat 配置升级 | FR-002, FR-008, FR-012, NFR-002 | 自动归档/清默认/失效授权；旧快照在密钥和网络前 fail closed，旧 endpoint 调用 0 |
| 运行中取消与重启 | FR-005, FR-014–FR-016, NFR-003 | `cancel_requested` → 安全终态；重启无丢失/假成功 |
| 设置修订使授权失效 | FR-002, FR-008, NFR-002 | 修改地址/模型后旧授权不可路由云端 |

## 6. 研究/POC 追踪

| 研究门禁 | 关联需求/ADR | 当前状态 | 所需产物 |
|---|---|---|---|
| 30–50 视频平台/ASR POC | FR-003, FR-006, FR-009, FR-010; ADR-005/006/008 | 未运行 | direct-only 网络/地区、合法本地后备、yt-dlp/EJS/Deno 版本/哈希/solver 与缺失场景、腾讯标准录音识别和本地 ASR 同样本、Base64/COS 两路径、原始度量/账单、失败样本、签字结论 |
| 腾讯 ASR/COS 合同、价格与区域复核 | FR-008, FR-009, NFR-002 | 官方网页与 SDK 静态审计；无 live 请求 | 发布日控制台/合同记录；TC3 headers/body/status/result limits、COS 最小权限/生命周期/成本刷新 |
| YouTube JS 运行链 | FR-001, FR-003, NFR-010; ADR-005 | 官方 yt-dlp/EJS/Deno 静态审计；当前环境缺 EJS/Deno | D 盘受控资产、哈希/SBOM、禁系统 Node/远程 component、readiness 与真实公开 corpus |
| 国内付费 ASR 选择 | FR-009, FR-010, NFR-002 | 腾讯标准录音识别为 V1 唯一编译进产品的云 adapter；无质量/价格实测排名 | 先完成腾讯同样本真实账单、区域/保留/授权留证；其他国内厂商只在后续以显式 adapter 评估 |
| Bilibili 字幕适配风险 | FR-003, FR-006, NFR-010 | 官方目录 + yt-dlp/BiliNote 静态审计 | 真实公开 corpus、错误分类、平台 drift runbook |
| 百炼 chat 翻译/笔记质量 | FR-012, FR-013 | 官方北京 endpoint/结构化输出静态审计；无 live 请求 | 同工作空间模型连通性、cue 完整、意义保持、时间引用可解析率、事实覆盖/虚构率、人工双评 |
| Windows 性能矩阵 | NFR-005, NFR-008 | 仅依赖/FFmpeg 基础 | 批准模型/compute/VAD、GPU/CUDA、RTF、显存/内存、磁盘、模型下载；CPU 仅诊断 |
| 实际分发许可证 | NFR-010; ADR-013 | yt-dlp/EJS/Deno 许可已登记；FFmpeg 7.1.1 当前开发 buildconf 已确认 GPL v3+；非发行结论 | 实际 FFmpeg `-version/-buildconf`、yt-dlp/EJS/Deno 与其他 lock/wheel/binary/model 哈希、源码对应关系、SBOM、NOTICE、license bundle |

来源链接与历史更正集中在 [research-sources.md](research-sources.md)，参考项目采用/拒绝
记录在 [reference-projects.md](reference-projects.md)，技术取舍在
[technical-decisions.md](technical-decisions.md)。

## 7. 阈值追踪

| ID | Owner | 当前实施基线 | 发布冻结/验证任务 |
|---|---|---|---|
| THR-001 上传上限 | 产品 + Backend + Security | 8 GiB 媒体、16 MiB 字幕、32 KiB metadata、128 KiB overhead；总 request 公式见 ADR-014 | ingress 资源/失败测试与浏览器验收后冻结 |
| THR-002 云应用阈值 | ASR + Backend + 产品 | 编码音频 ≤4,500,000 B 走 Base64；较大且 ≤5 h、≤96 MiB 走私有 COS；请求体上限 64 MiB；精确公式与删除门禁见 ADR-014 | Base64/COS 两路径、真实峰值内存/时长/账单 POC 后冻结 |
| THR-003 AI 输入/输出 | AI + Security + 产品 | 30/15 cues、64 KiB prompt、48 KiB note chunk、24 chunks/4 levels、256 KiB response | provider capability 与最坏 cue corpus 后冻结 |
| THR-004 API p95/基准量 | Backend + QA | 100 tasks、热缓存、核心 API p95 ≤250 ms | 固定设备重复基准后冻结 |
| THR-005 轮询负载 | Frontend + Backend + QA | 前台 1/2/5 s、后台 15 s、错误 2/4/8/16/30 s、单一在途 | 多标签/后台节流测试后冻结 |

这些数值只授权实现；正式发布结论仍以 POC 和 ADR-014 的签字门禁为准。

## 8. 基线验证

本轮文档工作开始前，在 Conda 环境
`D:\ProgramData\Anaconda3\envs\vtnote\python.exe` 使用专用 basetemp 运行完整测试：

```text
python -m pytest -q -p no:cacheprovider
238 passed, 1 warning in 10.38s
```

warning 是现有 `pyproject.toml` 中 pytest 不识别 `cache_dir` 配置项；本任务不改生产
配置或依赖。最终提交前必须重新运行完整测试，并在任务报告记录新的命令、专用 temp
路径、结果、Git diff 检查和文档一致性检查。
