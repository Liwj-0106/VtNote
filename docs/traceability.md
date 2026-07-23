# VtNote V1 需求追踪矩阵

状态：基线 HEAD 审计
Owner：VtNote 产品/QA 负责人
基线：`2a36eb0ac0419caf1a9b297c90bfb1e7d0baf8d7`
证据截止：2026-07-24
需求来源：[product-requirements.md](product-requirements.md)
实现计划：[2026-07-18-vtnote-v1.md](superpowers/plans/2026-07-18-vtnote-v1.md)

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
| FR-001 | 首次启动与就绪检查 | Task 2, 5, 6 | 部分实现 | storage/env 配置与 `tests/test_environment_contract.py`, `test_storage.py`；无 setup UI/launcher | FFmpeg/目录/模型三态 UI；缺失组件与部分可用 E2E；启动器仅回环 |
| FR-002 | 连接、profile、默认项、修订快照 | Task 2, 4, 5 | 部分实现 | `configuration.py`；`test_configuration.py`, `test_configuration_lifecycle.py`, `test_api.py` 覆盖 keyring、测试/授权修订、归档、默认项 | 真实连接测试 adapter；设置 UI；修订失效 E2E；secret DOM/network 扫描 |
| FR-003 | URL 校验与安全探测 | Task 2, 3, 6 | 部分实现 | `url_security.py`; `test_url_security.py`; API 注入探测和 redirect 报告重校验测试；一次 direct-only YouTube 超时仅为环境观察 | `trust_env=false` transport、逐连接 DNS pin、受控 redirect、yt-dlp 网络边界；中国网络 direct-only corpus 与本地文件后备；代理另立 ADR |
| FR-004 | 浏览器上传与受信本机文件 | Task 3, 5, 6 | 部分实现 | `uploads.py`; `test_uploads.py` 覆盖流式大小限制、sniff、失败回收、原件只读、UNC 拒绝 | 网站文件选择/上传取消；跨进程/崩溃 E2E；普通 task/item API 不泄露来源绝对路径；setup/storage 只返回应用根 |
| FR-005 | 持久 task/item/stage、快照和历史 | Task 2, 3, 5 | 部分实现 | `tasks.py`, `models.py`; `test_tasks.py::test_enqueue_creates_durable_rows_and_immutable_redacted_snapshot`; API list/get | lease worker 与重启恢复；创建禁重/成功跳转；历史和详情 UI |
| FR-006 | 固定字幕优先顺序 | Task 1, 3, 7 | 部分实现 | `sources.py`; `test_source_contracts.py` 覆盖人工/自动、语言/格式顺序和音频只取一次 | 真实 Bilibili/YouTube adapter；有字幕 corpus 的音频/ASR 调用数为 0 |
| FR-007 | 音频取得与 FFmpeg 预处理 | Task 3, 6 | 部分实现 | `media.py`; `test_media.py` 覆盖 shell-free、超时、真实 FFmpeg、部分文件隔离、原子发布；开发机 7.1.1 buildconf 已只读记录 | 平台 audio handoff；worker 取消/重试；资源/磁盘故障 E2E；实际发行 FFmpeg build/SBOM/NOTICE 审计 |
| FR-008 | ASR 路由、修订授权、费用提示 | Task 2, 3, 5 | 部分实现 | `configuration.py`, `tasks.py`; `test_profiles_enforce_protocol_and_upload_consent_revision`, `test_auto_mode_omits_cloud_profile_until_upload_is_authorized` 证明无授权时 auto 快照不含云 profile | 创建页显示 auto 落本地且仍可提交、仅 cloud 阻塞；worker 按快照路由；未授权网络调用为 0 |
| FR-009 | 火山极速版 Base64 ASR | Task 3, 7 | 部分实现 | 固定 resource/config 与云音频原语；`test_pipeline_contract.py::test_volc_resource_is_fixed_and_not_caller_configurable`; `test_real_ffmpeg_cloud_conversion...` | 16k mono OGG/Opus、`audio.data`、`bigmodel`、时长/二进制/Base64/语言预检；auto/cloud 分类、429/5xx/config/unknown；脱敏 logid、无 raw response；live POC |
| FR-010 | faster-whisper 与云转本地 | Task 3, 7 | 部分实现 | 依赖精确 pin；配置/测试已有 `large-v3-turbo/int8_float16/VAD` 与 D 盘根；当前 `device:auto` 尚未落实 GPU 必需；无生产 transcribe 调用 | segment、GPU concurrency=1、固定模型/CUDA、禁止静默 CPU、取消/provenance、明确失败 fallback、unknown 不重发 |
| FR-011 | 不可变规范转录 | Task 1 | 已实现（当前范围） | `schemas.py`, `artifacts.py`; `test_schemas.py`, `test_storage.py::test_transcript_write_is_immutable...`, export tests | 在真实字幕/云/本地三条链路做 E2E；schema 兼容与产物唯一性门禁 |
| FR-012 | 可选 cue 对齐翻译 | Task 1, 2, 4, 5 | 部分实现 | translation schema/hash、配置快照、平行 stage、按需译文导出已有测试 | chat adapter、chunk/一次小 batch retry、结构错误隔离、UI、真实 provider contract |
| FR-013 | 可选 AI 笔记 | Task 2, 4, 5 | 部分实现 | 内部 `summary/key_points/custom`、`zh-Hans`、profile 后一次性自动启用、parallel stage 与 note typed path 已有测试 | 三个中文固定标签、直接读原文、顺序 chunk/map/reduce、每个时间引用解析到 cue、生成/UI/质量 POC |
| FR-014 | 进度、决策、错误与警告 | Task 2, 3, 5, 6 | 部分实现 | pipeline 状态聚合、stage attempts、warning/diagnostic sanitize 与 API | worker heartbeat/progress；全部错误码映射；详情 UI、轮询退避、重启恢复 E2E |
| FR-015 | 排队/运行取消 | Task 2, 3, 5 | 部分实现 | 现有测试覆盖 queued→canceled、running→cancel_requested 与 cancel_requested 重复请求；当前 HEAD 对 canceled 后重复请求仍拒绝，尚未达到目标合同 | 补“因取消已 canceled 时返回当前资源、其他终态拒绝”测试/实现；worker 安全检查点、部分资产、UI |
| FR-016 | 阶段重试与冲突 | Task 2, 3, 4, 5 | 部分实现 | `test_retry_is_stage_only...`, active/dependent conflict、parallel branch、duplicate race 测试 | worker 真执行；上游产物失效；cloud unknown 二次计费保护；UI |
| FR-017 | 查看与确定性导出 | Task 1, 2, 5 | 部分实现 | `exports.py`; `test_exports.py`; `test_original_and_translation_exports_are_generated_on_demand`; API export | transcript/translation/notes viewer；下载菜单/文件名；长文性能和格式错误 UI |
| FR-018 | D 盘存储、回收、恢复与日志 | Task 1, 3, 5, 6 | 部分实现 | typed paths；`test_runtime_assets.py` 覆盖 trash/restore/24h purge/reparse/并发；upload/media cleanup | worker/launcher 接入；设置页占用/回收；计划清理与恢复 E2E；用户原件扫描 |
| FR-019 | 执行摘要与 provenance | Task 1, 2, 3, 4, 5 | 部分实现 | provenance schema、pipeline snapshot、stage attempts、sanitized public view 与基础确定性 Markdown 导出；现有 Markdown 未包含完整来源证明 | adapter/model/request/fallback/authorization 全链写入；脱敏 X-Tt-Logid、无 raw cloud response；JSON/Markdown 完整安全来源证明；详情呈现；无遥测 |

## 3. 非功能需求追踪

| ID | 需求摘要 | 计划任务 | 当前状态 | 当前代码/测试证据 | 发布门禁 |
|---|---|---|---|---|---|
| NFR-001 | 回环、同源、Host/Origin/CSRF | Task 2, 5, 6 | 部分实现 | `test_exact_host_origin_and_double_submit_csrf_are_enforced`; bind 设置拒绝非回环；docs 默认关闭 | 生产启动器只监听 `127.0.0.1:8765`；静态同源；无宽松 CORS；浏览器 E2E |
| NFR-002 | keyring、密钥零泄漏、上传隐私 | Task 2, 3, 5, 6, 7 | 部分实现 | keyring 抽象/补偿；API、task、diagnostic redaction；configuration lifecycle tests | worker/cloud/log/UI/诊断包 secret scan；未授权云网络调用 0；外部合同复核 |
| NFR-003 | durable worker、重启恢复、幂等 | Task 2, 3, 6, 7 | 部分实现 | SQLite task/stage 与 runtime asset crash-shape 原语；没有 lease worker | kill -9/断电形状故障注入；lease/heartbeat/重领；外部 unknown outcome；无丢任务 |
| NFR-004 | 路径、命令、原件与回收安全 | Task 1, 3, 6, 7 | 部分实现 | typed paths、reparse/junction 防护、shell-free runner、原件只读、recoverable trash 测试 | 全链安全回归、launcher/cleanup、恶意文件名/路径 corpus、原件哈希不变 |
| NFR-005 | Windows/Python/GPU 兼容 | Task 3, 6, 7 | 部分实现 | Python 范围与精确依赖 pin；环境记录 native runtime；真实 FFmpeg 测试 | Win10/11 x64、批准默认与 GPU/CUDA 固定矩阵；安装/首次模型下载 E2E；CPU 仅诊断/未来备选 |
| NFR-006 | 键盘、AA、缩放、减弱动画 | Task 5, 7 | 未实现 | 当前无 React UI | 键盘核心旅程、焦点、屏幕阅读器、axe/人工 AA、200% 与 4 视口 |
| NFR-007 | 结构化脱敏日志与诊断包 | Task 2, 3, 6, 7 | 部分实现 | `sanitize_diagnostic` 写入边界与统一 API error shape 测试 | worker/launcher 结构化日志、轮转、关联 ID、限长 provider logid、禁止 raw cloud response、诊断包与全局 secret/path scan |
| NFR-008 | worker 性能与诚实进度 | Task 3, 5, 7 | 未实现（仅合同） | 长任务未执行；UI 未实现；media runner 有界 | POC RTF/内存/磁盘/轮询；无假百分比；API 交互基准 |
| NFR-009 | 显式 adapter、版本化、测试先行 | Task 1–7 | 部分实现 | typed protocols、schema、迁移、238 项基线测试；真实 adapter/orchestration/UI 未完成 | 全套单元/合同/集成/E2E/安全；依赖锁与迁移复核；review 关闭 |
| NFR-010 | 条款、版权、依赖许可与无绕过 | Task 3, 5, 6, 7 | 持续门禁 | URL allowlist、无 Cookie/plugin、参考项目许可审计；当前 FFmpeg GPL v3+ 开发 build 已记录 | UI 权利提示；实际发行 FFmpeg/依赖/模型 artifact 的 buildconf、源码、SBOM/NOTICE；平台/供应商条款复核 |

## 4. 计划任务到验收包

| 实现计划 | 交付边界 | 当前基线 | 进入下一任务前必须有 |
|---|---|---|---|
| Task 1 | transcript schema、parser、artifact、export、DB | 已完成并历经修复 | 规范/导出回归持续通过 |
| Task 2 | config/task/API/local security | 已完成并历经修复 | secret、revision、status、URL 输入策略回归 |
| Task 3 | worker、来源、Volc/local ASR、回收 | 仅 3A runtime asset 与 3B ingress/media 原语完成；主体未完成 | lease/heartbeat、真实 adapter、`trust_env=false`/逐连接网络边界、完整 Volc 分类与本地批准默认、fallback、取消/重试 E2E |
| Task 4 | 翻译、笔记、编排 | 未完成 | AI failure 不重跑 source/transcribe；固定模板/默认语言、顺序分块、cue 可解析引用、结构化产物/一次受控 retry |
| Task 5 | React 网站 | 未完成 | 三个顶层 surface、API client/component tests、静态 build |
| Task 6 | launcher、集成、安全、文档 | 未完成 | first-run、监督、日志、静态深链、安装/运行、FFmpeg 发行构建留证、安全回归 |
| Task 7 | 全项目 review/release | 未完成 | POC、全测试、实际 artifact 依赖/许可/secret scan、构建/启动 smoke、未决项签字 |

## 5. 用户旅程验收

| 旅程 | 需求 ID | 最小 E2E |
|---|---|---|
| URL 有人工首选语言字幕 | FR-003, FR-005, FR-006, FR-011, FR-017 | 探测 → 选轨 → 规范转录 → 导出；断言音频/ASR 0 调用 |
| URL 人工字幕坏、自动字幕可用 | FR-006, FR-014, FR-019 | 下一候选成功；警告与最终选择可见 |
| 中国网络 direct-only URL 超时 | FR-003, FR-004, NFR-010 | 不继承代理、不外推平台结论；错误带环境限定；合法本地文件后备可继续 |
| URL 无字幕、授权有效、云成功 | FR-007–FR-011, FR-019 | OGG/Opus Base64 → Volc mapping → 单一规范转录 |
| 火山预检不合格 | FR-008–FR-010, FR-014 | 时长/二进制/Base64/语言发送前判定；auto 本地、cloud 阻止；云调用 0 |
| 云明确失败、auto 转本地 | FR-008–FR-010, FR-014 | 网络未受理/明确 429/5xx 后本地成功、fallback provenance；配置错停止修复 |
| 云结果未知 | FR-009, FR-010, FR-016, FR-019 | 不自动再云请求；可转本地；可能计费警告保持 |
| 本地媒体仅本地 | FR-004, FR-007, FR-010, FR-018 | 原件哈希不变、受控资产、本地转录 |
| 本地字幕 | FR-004, FR-006, FR-011 | 无 ASR、解析/规范化/导出 |
| 翻译失败、笔记成功 | FR-012–FR-014, FR-016 | 原文/笔记可用，item 为 warning，仅译文可重试 |
| 长字幕生成笔记 | FR-013, FR-017 | 原文 cue 顺序分块/合并；每个时间引用点击后定位同一规范 cue |
| 运行中取消与重启 | FR-005, FR-014–FR-016, NFR-003 | `cancel_requested` → 安全终态；重启无丢失/假成功 |
| 设置修订使授权失效 | FR-002, FR-008, NFR-002 | 修改地址/模型后旧授权不可路由云端 |

## 6. 研究/POC 追踪

| 研究门禁 | 关联需求/ADR | 当前状态 | 所需产物 |
|---|---|---|---|
| 30–50 视频平台/ASR POC | FR-003, FR-006, FR-009, FR-010; ADR-005/006/008 | 未运行 | direct-only 网络/地区、合法本地后备、批准本地默认、候选付费 ASR 同样本、固定版本、原始度量、失败样本、签字结论 |
| 火山合同/价格/区域复核 | FR-008, FR-009, NFR-002 | 仅官方网页审计 | 发布日控制台/合同记录；接口/limit/成本刷新 |
| 付费 ASR 候选矩阵 | FR-009, FR-010, NFR-002 | 一手来源有界对照；无质量/价格实测排名 | Volc provisional；OpenAI/AssemblyAI 只作自愿 POC；同样本真实账单、区域/保留/授权留证 |
| Bilibili 字幕适配风险 | FR-003, FR-006, NFR-010 | 官方目录 + yt-dlp/BiliNote 静态审计 | 真实公开 corpus、错误分类、平台 drift runbook |
| chat 翻译/笔记质量 | FR-012, FR-013 | 未运行 | cue 完整、意义保持、时间引用可解析率、事实覆盖/虚构率、人工双评 |
| Windows 性能矩阵 | NFR-005, NFR-008 | 仅依赖/FFmpeg 基础 | 批准模型/compute/VAD、GPU/CUDA、RTF、显存/内存、磁盘、模型下载；CPU 仅诊断 |
| 实际分发许可证 | NFR-010; ADR-013 | FFmpeg 7.1.1 当前开发 buildconf 已确认 GPL v3+；非发行结论 | 实际 FFmpeg `-version/-buildconf`、源码对应关系、lock、wheel/binary/model SBOM、NOTICE、license bundle |

来源链接与历史更正集中在 [research-sources.md](research-sources.md)，参考项目采用/拒绝
记录在 [reference-projects.md](reference-projects.md)，技术取舍在
[technical-decisions.md](technical-decisions.md)。

## 7. 未冻结阈值追踪

| ID | Owner | 当前证据 | 冻结/验证任务 |
|---|---|---|---|
| THR-001 上传上限 | 产品 + Backend + Security | 代码有当前 `UploadLimits` 默认值，但不是批准的发布承诺 | Task 3 ingress 资源/失败测试后、Task 5 校验前冻结 |
| THR-002 云应用阈值 | ASR + Backend + 产品 | 官方 2h/100MB 是上限、二进制尽量约 20MB 是建议；应用二进制/Base64/内存限制未冻结 | `4*ceil(n/3)`、JSON overhead、峰值内存/时长 POC 后、Volc HTTP 前冻结 |
| THR-003 AI 输入/输出 | AI + Security + 产品 | 只有“有界”合同，无批准数值 | Task 4 RED tests 前冻结并做最坏 cue corpus |
| THR-004 API p95/基准量 | Backend + QA | 无基准设备/任务数/目标 | Task 5 性能验收前冻结 |
| THR-005 轮询负载 | Frontend + Backend + QA | 无批准 interval/多标签负载 | 依赖 THR-004，在 Task 5 API client tests 前冻结 |

未冻结项不得被 UI 文案、README 或发布说明写成永久保证。

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
