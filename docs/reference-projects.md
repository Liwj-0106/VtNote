# VtNote 产品与技术调研

校准日期：2026-08-19
原则：只记录会影响当前产品或技术决策的结论；价格、热度和营销数字不作为架构依据。

## 结论

VtNote 不需要变成完整下载器或通用知识库。其差异化应保持为：

- 本机任务和规范字幕长期保存；
- B 站链接与本地音视频走同一可靠流水线；
- 字幕、音频和 AI 笔记独立选择；
- 平台字幕优先，云/本地 ASR 明确后备；
- 失败后保留部分成果，并能解释真实执行路径；
- 密钥、媒体缓存和规范文本具有清楚的数据边界。

## 产品调研

| 产品/项目 | 可观察能力 | 对 VtNote 的启发 | 不采用或不照搬 |
|---|---|---|---|
| [BiliNote](https://github.com/JefferyHcool/BiliNote) | 视频链接到 Markdown 笔记、截图/原片跳转、AI 问答，提供本地部署和托管版 | 笔记应保持结构、来源定位和后续复用能力 | VtNote 当前不扩大到截图、RAG、托管 SaaS；先保证字幕和本机恢复 |
| [BibiGPT Skill](https://github.com/JimmyLv/bibigpt-skill) | URL 摘要、章节、字幕、批处理、MCP/CLI/API | 字幕-only、章节和机器可读输出应是独立意图 | 远程服务、OAuth/MCP 和多平台批量不是当前本机 V1 范围 |
| [NotebookLM](https://support.google.com/notebooklm/answer/16164461) | 多来源研究、基于来源的问答与引用、报告/导览等派生产物 | 长期方向是让字幕成为可引用来源，而不是只生成一次性摘要 | YouTube 导入依赖公开字幕；云端知识库的数据模型和隐私边界不同，不作为当前架构模板 |
| DownKyi 1.6.1 | B 站解析、字幕/音视频独立选择、下载进度、断点恢复、FFmpeg 合并 | 内容选择、阶段状态、字幕轨枚举、许可入口值得参考 | GPL 源码/二进制、aria2 RPC、旧私有 API 和 exe 目录存储不复用 |
| vivo 录音机许可清单 | Android UI、网络、协程、容器/Office 解析等依赖披露 | 可借鉴结构化并发、取消、流式网络和依赖清单生成 | 清单不包含录音/ASR 架构证据，不能据此判断其识别方案更优 |

产品结论：当前设置中的“默认生成与导出类型”是正确方向；字幕不应隐含 AI。下一阶段的产品价值优先级应是字幕质量/选轨、缓存治理、详情与可追溯性，而不是继续增加 Provider 数量。

## 技术选型调研

### 平台解析与下载

| 方案 | 优点 | 风险 | 决策 |
|---|---|---|---|
| 固定 yt-dlp + adapter | 平台变化集中在上游；支持字幕/音频信息；便于固定版本与测试 | 仍受平台变化、EJS 运行时和网络环境影响 | **当前采用**；外层继续做 URL/DNS/redirect/resource 校验 |
| 自写 B 站私有 API | 可精细控制字幕轨和 DASH | WBI/端点/Cookie 易漂移，长期维护和登录边界更大 | 可做无登录、只读实验 adapter；实测优于 yt-dlp 后才启用，并保留后备 |
| aria2 | 多连接下载、GID/session 恢复成熟 | 新进程/RPC/证书配置与治理成本；和现有 Worker durability 重复 | 当前单音频路径无实测收益；大文件基准证明收益后可作为 loopback-only 可选下载器 |
| 浏览器扩展/读取 Cookie | 可接近用户登录态 | 密钥/会话泄露和平台合规风险高 | V1 禁止 |

### ASR

| 方案 | 特征 | 决策 |
|---|---|---|
| 腾讯云录音文件识别 | 异步 task id，可内联或通过私有 COS；适合较长录音 | **当前云路径**；必须持久化 submit/query/unknown/cleanup，验证可能计费 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + CTranslate2 | 本地模型、GPU/量化选项、生态成熟 | **当前本地后备**；固定模型资产并由用户显式安装，不在任务中临时下载 |
| whisper.cpp | 单二进制、CPU/多平台潜力 | 需要另一套模型格式、质量/性能矩阵和分发验证 | 候选，不与当前引擎同时维护 |
| FunASR/SenseVoice 类方案 | 中文场景潜力 | 新模型许可、服务进程、结果 schema 和实测成本 | 只有真实语料证明收益后再评估 |

当前选择不是“全场景准确率冠军”的声明。云/本地质量必须在相同授权语料上比较；项目不引用不同厂商不可比的营销准确率。

### vivo 录音机开源清单的启发边界

截图中的 appcompat、Material、RecyclerView、Lottie 等主要是 Android UI；Gson、OkHttp/Okio、Kotlin coroutines、commons-compress、isoparser、Apache POI/StAX 分别对应 JSON、网络流、结构化并发、压缩、MP4 容器和 Office/XML。它们证明 vivo 做了依赖披露，但没有出现录音引擎、音频编解码器、降噪或 ASR 模型，因此不能反推出录音机识别架构。

对 VtNote 真正有用的是设计思想而不是 Java/Kotlin 依赖替换：HTTPX 已覆盖连接池、流式读取和取消；Worker lease/checkpoint 应继续贯彻 coroutines 式结构化取消；FFprobe 已比单独引入 isoparser 更适合当前多容器探测。Office/动画/UI 依赖与本次字幕失败无关，不引入。vivo 官方把 OSPO 和开源项目放在独立门户，可作为未来自动生成依赖清单的产品参考：[vivo OSPO](https://tech.vivo.com/OSPO/)、[vivo Open Source](https://opensource.vivo.com/)。许可展示页面按当前产品优先级延期。

### 数据和部署

| 方案 | 决策 |
|---|---|
| SQLite WAL + 本机文件 | 当前采用，适合单用户和一个独立 Worker；任务与文件通过 ID/相对路径关联 |
| PostgreSQL + 对象存储 | 仅当需要多用户、远程 Worker、云同步或跨设备访问时重审；不是当前已选架构 |
| 文本导出持久化多份 | 不采用；规范 JSON/Markdown 长期保存，SRT/TXT 等按请求生成 |
| Cache 无条件可删 | 不成立；active 媒体可能是唯一音频成果，必须先实现策略和一致性修复 |

## DownKyi 1.6.1 本地快照审计

审阅范围：

- `D:\Workspace\Project\CY\downkyi--main`：源码快照，无 `.git` 元数据。
- `D:\Workspace\Project\CY\DownKyi-1.6.1`：发行目录，包含用户状态。

源码 `AssemblyInfo.cs` 与发行文件均标识 1.6.1，`CHANGELOG.md` 日期为 2023-12-10。两个目录只能证明本地快照内容，不能证明完整上游提交；上游 release 页面仍将 v1.6.1 标为最新 release：[DownKyi releases](https://github.com/leiurayer/downkyi/releases)。

### 架构观察

- Windows WPF / .NET Framework 4.7.2。
- Prism/DryIoc MVVM、View/ViewModel、服务注册和区域导航。
- `DownKyi.Core` 负责 Bili API、DASH、字幕、下载器、FFmpeg、设置和 SQLite。
- 字幕通过 `x/player/wbi/v2` 枚举 `subtitle_url`，把 B 站 JSON body 转为 SRT；没有 ASR 或大模型。
- 下载使用内建 Range 多线程或 aria2 JSON-RPC，随后 FFmpeg stream-copy 合并/提取。
- 下载队列、完成历史、header/cover 和设置保存在 exe 目录下的 SQLite/配置/缓存文件。

### 可以借鉴

| 思想 | VtNote 落点 |
|---|---|
| 音频/字幕等内容独立选择 | 已由生成与导出偏好实现；继续保持字幕与 AI 解耦 |
| 清晰阶段与进度 | 映射到 stage run、progress 和内容库行内状态 |
| external GID/session 恢复 | 使用现有 external request id、cloud submission、lease/recovery，不引入 aria2 |
| 枚举所有字幕语言轨 | 在 typed adapter 中保留语言、来源和选择规则，自行实现并测试 |
| About 第三方许可入口 | 当前延期；未来若实现，由 SBOM/NOTICE 生成，不手工硬编码八项表 |
| FFmpeg stream-copy 快路径 | 在参数数组、输入校验和原子 staging 边界内评估 |

### 采用门禁

- 项目根 `LICENSE` 是 GPL-3.0。GNU FAQ 明确允许修改后仅作个人/组织内部使用而不公开源码；一旦对外分发，义务重新生效。因此内部实验可以做，但复制代码必须记录来源、隔离变更，并把“是否分发”作为发布门禁：[GNU GPL FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#GPLRequireSourcePostedPublic)。
- 发行 FFmpeg 自报 GPL-3.0-or-later 并启用 x264/x265，但 VtNote 当前只需解码、抽音频和 Opus/AAC/MP3，不会因该构建获得本次 ASR 路径的收益；不替换现有开发构建。
- aria2 可以实验，但 DownKyi 的监听全部地址、任意 Origin、关闭证书检查和静态 token 配置绝不采用。aria2 官方默认只监听 loopback、默认不允许任意 Origin，并强烈建议设置 `rpc-secret`：[aria2 RPC 手册](https://aria2.github.io/manual/en/html/aria2c.html#rpc-options)。
- 固定密码保护设置、`BinaryFormatter` BLOB、字符串 SQL、同步阻塞进程和先删除目标/源文件的 FFmpeg 流程不采用。
- 2023 年 B 站私有接口可以作为无登录实验 adapter 的参考，但不能成为唯一生产路径；必须有合同测试、版本开关和 yt-dlp 后备。
- 源码快照缺少 NuGet packages，并引用不存在的 `Brotli.Core.csproj`，不能宣称可复现构建。

结论：DownKyi **可以参考，也允许做内部效果实验**；当前没有证据表明 aria2、其 FFmpeg 或私有接口能改善此次 ASR 失败，因此不为本次修复引入。后续只有基准证明净收益，且安全/恢复边界达标，才进入可选实现。

## 后续调研门禁

新增平台、ASR 或模型前必须回答：

1. 是否解决当前用户的高优先级问题，而不是增加配置项？
2. 是否有明确许可证、数据流、费用和数据保留说明？
3. 是否能映射到当前 typed adapter 和持久阶段，不破坏防重复？
4. 是否用相同授权样本记录质量、耗时、失败率和资源占用？
5. 是否有删除/退出策略，避免永久保留半实现兼容代码？
