# VtNote 产品文档任务报告

日期：2026-07-24
分支：`feature/vtnote-v1`
初始任务基线：`2a36eb0ac0419caf1a9b297c90bfb1e7d0baf8d7`
Important 审查修订基线：`3240f5679976bd85681bd504ff2f505877a98cc6`
最终外部合同复审基线：`5ec52f8`

## 1. 任务结果

本任务完成了 VtNote V1 的产品、网站、技术决策、参考项目、研究来源和需求追踪基线，并把两份旧研究报告明确标记为历史材料。交付物以当前代码和测试为实现边界，没有把规划中的 worker、云端适配器或 UI 描述成已完成能力，也没有修改生产代码、测试、依赖或项目配置。

新增文档：

- `docs/product-requirements.md`：V1、V1.1、Later 和明确不做事项；用户旅程；FR-001～FR-019；NFR-001～NFR-010；验收门槛；30～50 个视频的可行性验证；THR-001～THR-005 待冻结阈值。
- `docs/website-specification.md`：工作区创建/历史、详情、设置三个顶层界面；路由、状态、交互、错误恢复、隐私和可访问性约束。
- `docs/technical-decisions.md`：十三项 ADR，覆盖分层、任务状态、字幕选择、上传授权、云端配置、取消、网络边界、导出、FFmpeg 分发审计和演进策略。
- `docs/reference-projects.md`：产品与组件分层比较，BiliNote 本地副本审计，以及采用、改造、仅参考和拒绝决策。
- `docs/research-sources.md`：SRC-001～SRC-039 来源登记、用户输入/本地证据、访问日期、版本/地区/币种/波动性和来源到文档结论的映射。
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

### Important 审查修订

- 用户提供的 `https://www.bilinote.net/` 已单列为高波动 BiliNote Pro 商业/内测营销
  输入。upstream README 明示的 Pro 链接是 `https://www.bilinote.app/`，因此 `.net`
  与 upstream、本地 2.4.4 archive 的身份、运营方、版本和代码关系均保持未知。
- 私有 ChatGPT 会话
  `https://chatgpt.com/g/g-p-6a4a5f6817148191b4f4c7dde86eb336/c/6a5adf72-7784-83ec-b45e-a2a4a618d57d`
  已原样登记。审查记录为 Web 跳登录、已登录 Chrome 被企业安全策略阻止；正文未读取、
  不作证据，等待用户导出/粘贴后再做差异核对。
- 平台 outbound HTTP 合同固定 `trust_env=false`，不继承系统/环境代理。当前中国网络
  环境的一次 YouTube 直连超时只作为环境观察；平台 POC 必须保留失败样本和本地文件
  后备。显式可信代理需要新的 ADR、威胁模型和用户同意。
- 本地 ASR 恢复已批准默认：`large-v3-turbo`、`int8_float16`、VAD、segment 时间戳、
  GPU worker 单并发；模型/CUDA 发行组合固定版本放 D 盘。POC 只验证/触发重审，不能
  静默改回 TBD；CPU 不是 V1 发布必选项。
- 火山路由补齐时长、编码后二进制、Base64、语言预检，`auto/cloud` 分流，429/5xx、
  配置错误和未知结果分类，脱敏 `X-Tt-Logid` 及禁止持久化原始响应等合同。
- 付费 ASR 候选矩阵把火山列为 V1 provisional；OpenAI/AssemblyAI 只作自愿 POC/
  研究对照，按相同样本、真实账单、账户地区和保留合同验证，不做无实测质量/价格排名。
- 本机只读审计确认 FFmpeg 7.1.1 开发构建含 `--enable-gpl --enable-version3`
  `--enable-libx264 --enable-libx265 --enable-shared --disable-static`，未见
  `--enable-nonfree`，因此当前构建按 GPL v3+ 审计。未来发行 artifact 必须单独冻结
  buildconf、对应源码、SBOM/NOTICE；VtNote 直接复用 FFmpeg，不自研 codec/封装。
- AI 笔记固定“综合总结/干货提炼/自定义提示词”；AI 配置成功后一次性默认开启且输出
  简体中文；长原文按时间顺序分块/合并，每个时间引用必须解析回规范 cue。
- yt-dlp Bilibili extractor 证据固定到 commit
  `997fa140840a08df3938b40da470c78049fef1f6`；WhisperX 版本增加 PyPI 证据；
  youtube-transcript-api 因 YouTube-only、非官方站点行为和第二套网络/漂移合同而
  仅参考、不采用。

### 最终外部合同复审

- 火山极速版合同补齐 `X-Api-Sequence: -1`、请求体 `user.uid`/AppKey 密钥边界和
  `X-Api-Status-Code` 业务判定。HTTP 200 不再被视为业务成功；`20000000`、静音
  `20000003`、参数/空音频/格式 `45000001/2/151`、繁忙/内部 `55000031/550xxxx`
  以及未知合法码、缺失/畸形 status、非法成功 body 均有明确停止、fallback、
  未知计费和禁止自动云重发合同。
- yt-dlp 2026.7.4 的官方包说明要求完整 YouTube 支持具备 `yt-dlp-ejs` 和受支持
  JavaScript runtime。V1 初始研究组合固定 `yt-dlp-ejs==0.8.0` 与 Deno 2.8.1，
  wheel、单文件、哈希和 Deno cache 置于 D 盘；禁止自动更新、C 盘系统 Node 兜底和
  运行时远程 EJS component。当前环境缺 EJS/Deno，因此此能力仍是 POC/发行门禁，
  不是已实现声明。
- 增加 yt-dlp、yt-dlp-ejs、Deno 的官方版本、安装与许可证来源，来源登记扩展至
  SRC-039；OpenAI 音频输出格式的官方材料差异改为逐模型 capability test，不再作
  绝对格式结论。

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
- `trust_env=false` 平台 transport、受控 yt-dlp/EJS/Deno 运行链、火山完整
  header/body/provider-status 分类、本地 ASR 批准默认、笔记 cue 引用和发行
  FFmpeg 合规门禁都只是本轮文档合同，不代表代码已实现。

## 4. 验证证据

- 初始基线：`238 passed`，1 条既有的未知 pytest `cache_dir` 配置警告，耗时 10.38 秒。
- 提交前最终完整测试：`238 passed`，同一条既有警告，耗时 13.20 秒。
- Important 修订最终完整测试：`238 passed in 11.57s`；`compileall` 通过，
  `pip check` 返回 `No broken requirements found.`。
- 最终外部合同复审初次验证为 `238 passed in 11.71s`；关闭最后一项未知 provider
  status 映射和来源消费映射后，再次完整验证为 `238 passed in 13.28s`。两次都只有
  既有的 pytest `cache_dir` 配置警告；`compileall`、`pip check` 和
  `git diff --check` 通过。29 项 FR/NFR、
  5 项 THR、13 项 ADR、39 项 SRC 及其追踪/来源映射差异均为 0；相对链接、BOM、
  尾随空白、遗留 live-citation token 和范围外变更均为 0。
- `python -m compileall -q src tests` 通过，`python -m pip check` 返回 `No broken requirements found.`。
- 六份核心交付文档中的需求标识共 29 项，与追踪矩阵完全一致，差异为 0。
- THR 标识共 5 项，与追踪矩阵完全一致，差异为 0。
- 初始提交时 SRC 标识共 26 项；Important 修订扩展到 SRC-034，最终合同复审扩展到
  SRC-039。最终来源登记与来源
  消费映射差异为 0。
- 新增核心文档中遗留 `turn…` 引用为 0，UTF-8 BOM 文件为 0，尾随空白为 0。
- Important 修订严格只改变 8 份允许的研究/产品文档；相对链接断链为 0；两份旧研究
  报告均包含历史材料说明。
- Python 环境使用现有 Conda 环境 `vtnote`；pytest 临时目录和字节码缓存均写入 `D:\Workspace\Codex\cache\VtNote-product-docs\` 下的专用目录。
- Important 修订复核的 pytest 临时目录与字节码缓存写入
  `D:\Workspace\Codex\cache\VtNote-product-docs-final\`；未写入 C 盘项目文件。
- 最终复审的测试、字节码和临时文件写入
  `D:\Workspace\Codex\cache\VtNote-product-docs-final\validation-20260724-03\` 与
  `D:\Workspace\Codex\cache\VtNote-product-docs-final\validation-20260724-04\`。

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
- 当前网络的一次 YouTube 直连超时不是平台定论；中国目标环境的 direct-only 结果尚未
  形成分布。V1 不提供代理；本地文件是可控后备。
- 当前环境缺 `yt-dlp-ejs` 与 Deno，不能宣称具备完整 YouTube 支持；受控 D 盘运行链
  必须先通过版本/哈希/许可证/readiness 与真实公开 corpus 门禁。
- 火山 HTTP 状态不足以判断业务成功；正式实现和发布必须按
  `X-Api-Status-Code` 与响应体双重校验，并保留未知结果可能计费语义。
- 当前开发 FFmpeg 是 GPL v3+ 构建观察，不能直接进入发行包；发行构建和合规路径尚未
  冻结。
- 五项待定阈值必须由对应负责人基于实测冻结，不能以文档示例替代发布决策。
- 本任务只形成可执行基线，不代表 Tasks 4～7 的实现已经完成。

## 7. 提交约束

本任务只暂存并提交上述十个文档文件，形成一个聚焦提交；不推送远端，不改动生产代码、测试、依赖、项目配置或用户数据。
