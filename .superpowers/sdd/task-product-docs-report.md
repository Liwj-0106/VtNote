# VtNote 产品文档任务报告

日期：2026-07-24
分支：`feature/vtnote-v1`
任务基线：`2a36eb0ac0419caf1a9b297c90bfb1e7d0baf8d7`

## 1. 任务结果

本任务完成了 VtNote V1 的产品、网站、技术决策、参考项目、研究来源和需求追踪基线，并把两份旧研究报告明确标记为历史材料。交付物以当前代码和测试为实现边界，没有把规划中的 worker、云端适配器或 UI 描述成已完成能力，也没有修改生产代码、测试、依赖或项目配置。

新增文档：

- `docs/product-requirements.md`：V1、V1.1、Later 和明确不做事项；用户旅程；FR-001～FR-019；NFR-001～NFR-010；验收门槛；30～50 个视频的可行性验证；THR-001～THR-005 待冻结阈值。
- `docs/website-specification.md`：工作区创建/历史、详情、设置三个顶层界面；路由、状态、交互、错误恢复、隐私和可访问性约束。
- `docs/technical-decisions.md`：十二项 ADR，覆盖分层、任务状态、字幕选择、上传授权、云端配置、取消、网络边界、导出和演进策略。
- `docs/reference-projects.md`：产品与组件分层比较，BiliNote 本地副本审计，以及采用、改造、仅参考和拒绝决策。
- `docs/research-sources.md`：SRC-001～SRC-026 一手来源登记、访问日期、版本/地区/币种/波动性和来源到文档结论的映射。
- `docs/traceability.md`：全部 29 项 FR/NFR 到 Tasks 1～7、当前代码/测试证据和剩余验收工作的映射。
- `.superpowers/sdd/task-product-docs-report.md`：本任务范围、验证、风险和异常处理记录。

修改文档：

- `docs/deep-research-report-1.md` 与 `docs/deep-research-report-2.md`：只在开头增加历史材料说明；原正文保留，未把其中旧价格、旧能力描述或 `turn…` 引用当作当前事实。
- `docs/implementation-log.md`：记录本次文档交付、研究纠正、验证证据和外部配置回滚。

未删除仓库文件，也未复制第三方源代码。

## 2. 研究与纠正结论

- 本地 `D:\Workspace\Project\BiliNote-master` 没有 `.git` 元数据，只能确认其 README 自述版本为 2.4.4，不能证明它与某个上游提交或“最新版”等价。
- 对哔哩哔哩开放平台目录的限定审计没有发现面向任意公开视频的通用字幕接口。因此，基于网页提取器的哔哩哔哩适配器被定义为易受站点变化影响的尽力能力，而不是稳定官方 API。
- OpenAI 的结构化输出能力与具体模型/端点有关；“API 数据默认不用于训练”也不等于“从不保留数据”。文档分别记录能力探测、最小化发送和保留策略核验要求。
- Videosays 当前页面展示的是免费分钟数和按量付费，不再沿用旧报告中的人民币订阅档位。
- AssemblyAI 当前提供商业自托管部署选项，旧报告中的“无自托管”结论已失效。
- 火山引擎极速版按当前官方协议记录为 `X-Api-Key`、`volc.bigasr.auc_turbo`、V1 Base64 JSON `audio.data` 和规定音频格式；没有虚构 URL 模式或云端分片方案。
- yt-dlp、faster-whisper、WhisperX、VideoLingo 和 Argos Translate 的版本与许可证按官方仓库或 PyPI 记录，并保留二进制分发、传递依赖和模型资产的额外合规检查。

## 3. 当前实现边界

当前代码已覆盖基础 API、任务持久化、确定性字幕轨选择、受信本地路径解析、URL/DNS 预检查、云端配置测试状态、上传授权快照、取消请求和基础 Markdown 导出等能力。

以下内容仍是后续实现或发布门槛，不属于本次文档任务的完成项：

- worker 调度、下载、音频规范化、ASR、翻译和摘要流水线；
- 完整网站界面、轮询负载控制和端到端恢复体验；
- Bilibili、yt-dlp、OpenAI、火山引擎等真实适配器及其故障降级；
- 30～50 个代表性视频的可行性验证；
- THR-001～THR-005 的实测冻结；
- “因取消而已取消”的任务再次取消时返回当前资源的契约；当前实现仍会拒绝该重复请求；
- FR-019 要求的完整来源追踪导出；当前 Markdown 导出只有基础确定性内容。

## 4. 验证证据

- 初始基线：`238 passed`，1 条既有的未知 pytest `cache_dir` 配置警告，耗时 10.38 秒。
- 提交前最终完整测试：`238 passed`，同一条既有警告，耗时 13.20 秒。
- `python -m compileall -q src tests` 通过，`python -m pip check` 返回 `No broken requirements found.`。
- 六份核心交付文档中的需求标识共 29 项，与追踪矩阵完全一致，差异为 0。
- THR 标识共 5 项，与追踪矩阵完全一致，差异为 0。
- SRC 标识共 26 项，来源登记与来源消费映射差异为 0。
- 新增核心文档中遗留 `turn…` 引用为 0，UTF-8 BOM 文件为 0，尾随空白为 0。
- 八份受检文档的相对链接断链为 0；两份旧研究报告均包含历史材料说明。
- Python 环境使用现有 Conda 环境 `vtnote`；pytest 临时目录和字节码缓存均写入 `D:\Workspace\Codex\cache\VtNote-product-docs\` 下的专用目录。

## 5. 外部配置异常与回滚

研究过程中曾误执行一次 Codex MCP 注册命令，短暂修改了任务范围外的 `C:\Users\liweijie\.codex\config.toml`。发现后立即停止相关操作，并按以下方式完成精确回滚：

- 回滚前完整备份：`D:\Workspace\Codex\cache\VtNote-product-docs\backups\config.toml.before-openaiDeveloperDocs-remove-20260724T043230+0800.bak`
- 回滚日志：`D:\Workspace\Codex\cache\VtNote-product-docs\rollback-log.md`
- 备份 SHA-256：`3A930AC389984C4A6FAE4F9EEEF12059003411B9563401AF943D60B06980BD2C`
- 回滚后 SHA-256：`6A151094D77EEE8DFB0DD58E1A72E8E6CA2BBC0AAE5BC79787079454F46C12F8`
- 程序化比较确认：回滚后的文件等于备份文件仅删除目标 `openaiDeveloperDocs` 两行配置段及其后空行；其余内容未改变。
- 目标 MCP 条目已不存在，列表中只保留原有的 `node_repl`。

此异常没有修改仓库文件、用户数据或项目运行配置。备份和日志保留在指定缓存目录，便于审计。

## 6. 已知风险与后续门槛

- 外部服务的价格、限额、区域可用性、模型名称和保留政策具有时间敏感性，实施和发布前必须重新核验。
- Bilibili 网页提取路径容易受登录、风控、页面变化和地区差异影响，必须以 POC 成功率和错误样本决定是否进入 V1。
- 五项待定阈值必须由对应负责人基于实测冻结，不能以文档示例替代发布决策。
- 本任务只形成可执行基线，不代表 Tasks 4～7 的实现已经完成。

## 7. 提交约束

本任务只暂存并提交上述十个文档文件，形成一个聚焦提交；不推送远端，不改动生产代码、测试、依赖、项目配置或用户数据。
