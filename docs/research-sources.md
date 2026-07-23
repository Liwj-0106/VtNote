# VtNote V1 调研来源登记

状态：已审计的产品调研基线
Owner：VtNote 产品/技术负责人
证据截止：2026-07-24（Asia/Shanghai）
范围：`product-requirements.md`、`website-specification.md`、
`technical-decisions.md` 和 `reference-projects.md` 使用的外部事实

本登记把“观察到的事实”和“VtNote 决策”分开。链接只证明发布者在访问日公开陈述的
内容。价格、模型名、限制、平台支持与 “Latest” 徽标都可能变化，实现、发布或采购前
必须复核。搜索摘要和第三方比较文章不作为权威证据。

## 证据规则

- 优先使用发布者的产品页、API reference、仓库、release 页或 license 文件；
- 当地区、币种、版本会改变结论时，必须同时记录；
- 搜索不到某能力只能记为有边界的审计观察，不能证明该能力永远不存在；
- “当前实现”仅依据本仓库基线
  `2a36eb0ac0419caf1a9b297c90bfb1e7d0baf8d7`，不能从实现计划反推；
- 本地 BiliNote archive 没有 `.git`；README 自报 `BiliNote v2.4.4`，但无法证明其
  upstream commit 或与任何 GitHub release 等价；
- 本次调研没有复制 BiliNote 或其他第三方源码。

## 来源登记

| ID | 发布者/材料 | 本项目使用的事实 | 版本、地区或币种 | 波动性 | 访问日期 |
|---|---|---|---|---|---|
| [SRC-001](https://github.com/JefferyHcool/BiliNote) | BiliNote 仓库 | 产品能力与 upstream 项目身份；本地架构另行静态审计 | 本地 archive 自报 2.4.4；本地 commit 未知 | 高 | 2026-07-24 |
| [SRC-002](https://github.com/JefferyHcool/BiliNote/blob/master/LICENSE) | BiliNote license | upstream 为 MIT；复制实质部分需保留 notice | upstream default branch | 中 | 2026-07-24 |
| [SRC-003](https://openhome.bilibili.com/doc) | 哔哩哔哩开放平台 | 已发布目录包含授权、用户、稿件、数据、专栏与直播；审计目录未见任意公开视频通用字幕读取 API | 中国大陆服务 | 高 | 2026-07-24 |
| [SRC-004](https://github.com/yt-dlp/yt-dlp) | yt-dlp 仓库/README | 广泛 extractor、字幕列出/取得能力，以及二进制/依赖许可证注意事项 | VtNote pin：`yt-dlp==2026.7.4` | 很高 | 2026-07-24 |
| [SRC-005](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py) | yt-dlp Bilibili extractor | Bilibili 字幕实现使用网站/player endpoint，不是已发布的通用开放平台字幕合同 | 滚动 default branch | 很高 | 2026-07-24 |
| [SRC-006](https://github.com/SYSTRAN/faster-whisper) | faster-whisper 仓库 | CTranslate2 本地 Whisper、CPU/GPU 模式及 segment/word 数据 | VtNote pin：`faster-whisper==1.2.1`、`ctranslate2==4.8.1` | 高 | 2026-07-24 |
| [SRC-007](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE) | faster-whisper license | MIT 及 notice 保留义务 | default branch | 低 | 2026-07-24 |
| [SRC-008](https://github.com/m-bain/whisperX) | WhisperX 仓库 | 强制对齐、word timestamp、可选 diarization、额外模型及已声明限制 | PyPI 稳定版显示 3.8.6；3.8.7rc1 是预发布 | 高 | 2026-07-24 |
| [SRC-009](https://github.com/m-bain/whisperX/blob/main/LICENSE) | WhisperX license | BSD-2-Clause | default branch | 低 | 2026-07-24 |
| [SRC-010](https://github.com/Huanshere/VideoLingo) | VideoLingo 仓库 | 字幕切分、翻译、对齐、配音的端到端参考流程 | 未经新复核不固定 “latest” | 高 | 2026-07-24 |
| [SRC-011](https://github.com/Huanshere/VideoLingo/blob/main/LICENSE) | VideoLingo license | Apache-2.0 | default branch | 低 | 2026-07-24 |
| [SRC-012](https://pypi.org/project/argostranslate/) | Argos Translate PyPI | 离线翻译、可下载语言包、pivot translation；MIT/CC0 双许可 | 页面显示 1.11.0；包与模型版本独立 | 高 | 2026-07-24 |
| [SRC-013](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create) | OpenAI transcription API reference | 输入容器、按模型区分的输出格式、timestamp/diarization 约束 | 全球 API；模型行为可变 | 很高 | 2026-07-24 |
| [SRC-014](https://developers.openai.com/api/docs/guides/speech-to-text) | OpenAI speech-to-text guide | 支持流程与按模型区分的语音转文本行为 | 全球 API | 很高 | 2026-07-24 |
| [SRC-015](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint) | OpenAI data controls | API 数据默认不用于训练；endpoint 资格与保留控制取决于 endpoint/account | 合同/account 相关 | 很高 | 2026-07-24 |
| [SRC-016](https://openai.com/enterprise-privacy/) | OpenAI enterprise privacy | 默认不训练声明及 enterprise/API 隐私定位 | 合同/account 相关 | 高 | 2026-07-24 |
| [SRC-017](https://docs.volcengine.com/docs/6561/1631584?lang=zh) | 火山引擎录音文件极速版文档 | 单请求识别、header、endpoint、payload 与时长/大小/容器限制 | 中国大陆服务；页面显示 2026-06-26 更新 | 很高 | 2026-07-24 |
| [SRC-018](https://www.volcengine.com/product/asr) | 火山引擎 ASR 产品页 | 商业 ASR 定位和当前采购入口 | CNY；促销/计费会变 | 很高 | 2026-07-24 |
| [SRC-019](https://docs.bibigpt.co/getting-started/bibigpt-supported-platforms) | BibiGPT 文档 | 支持的平台链接与本地文件流程 | 商业服务；限制会变 | 很高 | 2026-07-24 |
| [SRC-020](https://api.bibigpt.co/) | BibiGPT API 文档 | 存在 API/产品集成；使用前需复核当前合同 | 商业服务 | 很高 | 2026-07-24 |
| [SRC-021](https://videosays.com/) | Videosays 产品页 | 公开链接转录、时间戳、TXT/SRT/VTT、5 分钟试用、按量措辞和不可访问链接边界 | 商业服务；未记录稳定 tier 价格 | 很高 | 2026-07-24 |
| [SRC-022](https://videosays.com/docs) | Videosays 开发文档 | REST/CLI 自动化入口 | 商业服务 | 很高 | 2026-07-24 |
| [SRC-023](https://www.assemblyai.com/pricing/) | AssemblyAI 价格页 | 当前模型/能力/用量价格；页面显示 Universal-3.5 Pro async USD 0.21/小时、Universal-2 USD 0.15/小时 | USD、按量；未含税/合同差异 | 很高 | 2026-07-24 |
| [SRC-024](https://www.assemblyai.com/deployments/self-hosted) | AssemblyAI self-hosted | 指定 API/模型现提供客户基础设施上的商业自托管；不是开源本地包 | enterprise/商业访问 | 很高 | 2026-07-24 |
| [SRC-025](https://www.assemblyai.com/docs/pre-recorded-audio/select-the-region) | AssemblyAI region guide | region 取决于 endpoint/workflow；数据驻留要求需另验 | US/EU offering | 高 | 2026-07-24 |
| [SRC-026](https://bibigpt.co/) | BibiGPT 产品页 | 转录、摘要、思维导图/聊天方向的产品对照 | 商业服务 | 很高 | 2026-07-24 |

### 来源使用映射

缩写：P=`product-requirements.md`，W=`website-specification.md`，
T=`technical-decisions.md`，R=`reference-projects.md`。W 中的外部事实通过 P/T 的
已审计结论使用；页面规格不自行扩大供应商声明。

| 来源 | 使用文档 | 来源 | 使用文档 |
|---|---|---|---|
| SRC-001 | R | SRC-014 | T, R |
| SRC-002 | R | SRC-015 | P, W, T, R |
| SRC-003 | P, W, T, R | SRC-016 | P, W, T, R |
| SRC-004 | P, T, R | SRC-017 | P, W, T, R |
| SRC-005 | P, T, R | SRC-018 | P, T, R |
| SRC-006 | P, T, R | SRC-019 | R |
| SRC-007 | R | SRC-020 | R |
| SRC-008 | P, T, R | SRC-021 | R |
| SRC-009 | R | SRC-022 | R |
| SRC-010 | P, T, R | SRC-023 | R |
| SRC-011 | R | SRC-024 | R |
| SRC-012 | P, T, R | SRC-025 | R |
| SRC-013 | T, R | SRC-026 | R |

## 本地证据登记

| ID | 证据 | 观察 |
|---|---|---|
| LOCAL-001 | `D:\Workspace\Project\BiliNote-master\README.md` | 标题自报 `BiliNote v2.4.4`；archive 无 Git metadata。 |
| LOCAL-002 | `D:\Workspace\Project\BiliNote-master\backend\app\services\note.py` | service 级编排把下载、转录、笔记、截图和文件 cache 组合在一起。 |
| LOCAL-003 | `D:\Workspace\Project\BiliNote-master\backend\app\downloaders\` | 存在 Bilibili、YouTube、本地媒体和字幕的独立 downloader。 |
| LOCAL-004 | `D:\Workspace\Project\BiliNote-master\backend\app\transcriber\` | provider factory 包含本地 faster-whisper 与在线 provider。 |
| LOCAL-005 | `D:\Workspace\Project\BiliNote-master\backend\app\services\task_serial_executor.py` | 工作执行依赖进程内状态，不是带 lease 的 durable DB worker。 |
| LOCAL-006 | `D:\Workspace\Project\BiliNote-master\backend\app\db\models\providers.py` | provider secret 由 SQLite-backed row 表示；UI 掩码不改变静态存储暴露。 |
| LOCAL-007 | `D:\Workspace\Project\BiliNote-master\backend\app\services\cookie_manager.py` | 存在 Cookie 与 JSON/file 流程，超出 VtNote V1 policy。 |
| LOCAL-008 | `D:\Workspace\Project\BiliNote-master\BillNote_frontend\src\pages\` | 创建、历史、onboarding、进度、转录、聊天和设置可作 UX 参考。 |
| LOCAL-009 | 基线 HEAD 的 `D:\Workspace\Project\VtNote\pyproject.toml` | pin 为 yt-dlp 2026.7.4、faster-whisper 1.2.1、CTranslate2 4.8.1。 |
| LOCAL-010 | 基线 HEAD 的 VtNote source/tests | 计划 Tasks 1、2、3A、3B 基础存在；live adapter、ASR 调用、worker loop、AI 执行、React UI、launcher 不存在。 |

## 历史报告纠错

两份 `deep-research-report-*.md` 作为历史发现材料保留。下列结论覆盖其中冲突内容。

<a id="cor-001--openai-transcription-response-formats"></a>

### COR-001 — OpenAI transcription 输出格式

历史材料把不同模型的格式混为一谈。当前
[OpenAI transcription reference](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)
把请求接受的音频容器和响应格式分别列出；在该 endpoint 中，
`gpt-4o-transcribe` 与 `gpt-4o-mini-transcribe` 只支持 `json` response format，
`whisper-1` 才与 `verbose_json`、SRT/VTT 等字幕文本格式相关。Diarization 与
timestamp granularities 还有额外的模型/格式限制。VtNote 必须按所选模型做 capability
test，不能从 endpoint 全部选项的并集推断某一模型能力。

<a id="cor-002--videosays-pricing-and-delivery-model"></a>

### COR-002 — Videosays 价格与交付模式

历史报告中的 RMB trial/starter 价格已不是当前证据。
[Videosays 当前产品页](https://videosays.com/)描述 5 分钟免费额度，之后一次性购买、
按量使用且无订阅；页面没有继续提供历史报告依赖的旧 tier。VtNote 不在成本比较中
使用旧数字；任何采购决策都需要在用户 billing region 重新取价/留证。

<a id="cor-003--assemblyai-self-hosting"></a>

### COR-003 — AssemblyAI 自托管

历史报告称 AssemblyAI 没有自托管能力，这一说法已过期。AssemblyAI 现在发布
[self-hosted deployment 页面](https://www.assemblyai.com/deployments/self-hosted)，
说明指定商业模型/API 可以部署到客户基础设施。它是 enterprise 商业部署，不是可自由
再分发的开源离线引擎；可用性、硬件、支持 API、region、价格和合同仍需与供应商确认。

<a id="cor-004--bilibili-subtitle-access"></a>

### COR-004 — Bilibili 字幕访问

在证据截止日，审计到的
[哔哩哔哩开放平台目录](https://openhome.bilibili.com/doc)没有发布面向任意公开视频的
通用第三方字幕读取 endpoint。这是有边界的审计发现，不是永久不存在的证明。
[yt-dlp extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)
与本地 BiliNote 快照都使用网站/player 行为。因此 VtNote 把 Bilibili URL/字幕支持
视为 best-effort、版本固定、由公开 corpus 测试的 adapter，并提供明确的
unsupported/auth/region 错误和本地文件后备；不能把它写成官方稳定 API 权利。

<a id="cor-005--bilinote-provenance"></a>

### COR-005 — BiliNote provenance

`D:\Workspace\Project\BiliNote-master` 自报 2.4.4，但没有 `.git`。其 commit SHA 以及
与任何 upstream release 的等价关系未知。upstream 页面和缓存索引对 “Latest” 可能
不一致，因此本审计不记录 upstream latest 版本。`reference-projects.md` 的能力观察
绑定到上面的本地路径，法律条款单独绑定到 upstream MIT license。

<a id="cor-006--openai-api-privacy-wording"></a>

### COR-006 — OpenAI API 隐私措辞

“默认不用于训练”不等于“从不保留”。
[data-control 文档](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint)
与 [enterprise privacy 页面](https://openai.com/enterprise-privacy/)区分训练默认值、
abuse-monitoring retention、endpoint eligibility 和 Zero Data Retention 等合同控制。
VtNote 必须显示所选 provider/profile 与上传授权状态，不能承诺一个跨 provider 的统一
保留期限。

## 调研缺口与刷新要求

- 尚未运行 30–50 视频验收 corpus；provider 质量、原生字幕覆盖、real-time factor、
  timeout 行为与成本均未测量；
- 未检查付费 provider account/region 合同；console quota、发票、税费、数据驻留与
  retention 取决于用户/account；
- Bilibili/YouTube extractor 天生易变；固定版本不能替代定期 corpus test 与
  unsupported-source fallback；
- Argos Translate 仅是 V1.1 候选；语言对模型包质量与再分发义务尚未验证；
- 本次审计中的 OpenAI 事实仅来自上表所列 OpenAI 官方网页，访问日期为 2026-07-24。
