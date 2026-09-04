# VtNote 调研来源

校准日期：2026-08-30

## 证据规则

1. 当前产品事实以代码、锁文件、数据库 schema 和通过的测试为最高优先级。
2. 第三方能力优先引用官方文档、官方仓库、release、锁文件或本地制品自报信息。
3. 易变的平台端点、产品功能和服务限制必须记录核对日期，不把一次观察写成永久合同。
4. 不使用不同厂商营销准确率作横向排名；质量结论需要相同授权样本。
5. 本地无 Git 快照只能证明所读文件，不能证明上游 commit 或完整可复现源码。

## VtNote 当前实现证据

| 主题 | 本地证据 |
|---|---|
| Python 直接依赖与版本 | `pyproject.toml`、`requirements.lock`、`environment.yml` |
| 前端依赖与版本 | `frontend/package.json`、`frontend/package-lock.json` |
| 当前页面和路由 | `frontend/src/app/App.tsx`、`Sidebar.tsx`、`pages/` |
| 任务阶段与生成选择 | `src/vtnote/tasks.py`、`pipeline.py`、`worker_store.py` |
| 存储根与固定路径 | `src/vtnote/config.py`、`paths.py`、`models.py` |
| 腾讯 ASR 合同 | `src/vtnote/tencent_contract.py`、`tencent_asr.py`、`transcribe_stage.py` |
| 本地 ASR | `src/vtnote/local_asr_contract.py`、`local_asr.py`、`sensevoice_asr.py`、`model_assets.py`、`assets/models/*.manifest.json` |
| 首屏设计与动效 | `design-system/vtnote/MASTER.md`、`design-system/pages/create-task.md`、`frontend/src/features/task-creation/LauncherSignal.tsx` |
| TokenHub | `src/vtnote/tokenhub_chat.py`、`ai_stages.py`、`frontend/src/pages/AiConnectionsPage.tsx` |
| 密钥与提示词保护 | `src/vtnote/secrets.py`、`provider_credentials.py`、`sensitive_text.py` |
| 许可制品证据 | `tools/collect_release_evidence.mjs`、实际 `ffmpeg -version/-buildconf` |

## 官方/上游来源

| 来源 | 用途 | 2026-08-19 采用结论 |
|---|---|---|
| [Tencent CreateRecTask](https://www.tencentcloud.com/document/product/1118/66925) | 录音文件识别提交、Base64/URL 与异步任务 | 采用异步任务边界；具体模型、大小和地域仍由本地固定合同与真实账户验证 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | CTranslate2 Whisper 推理、GPU/CPU/量化选项和 MIT 许可 | 保留固定 GPU profile 和用户显式开启的 CPU fallback；不在任务中下载模型 |
| [SenseVoice Small](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) | 非自回归多语种 ASR 模型卡与自定义模型许可入口 | 采用固定 INT8 ONNX 转换资产；上游性能数字不替代 VtNote 本机同样本验证 |
| [sherpa-onnx SenseVoice](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html) / [Python API](https://k2-fsa.github.io/sherpa/onnx/sense-voice/python-api.html) | SenseVoice 离线识别配置、模型文件和 Python 接口 | 固定 `sherpa-onnx==1.13.6`，使用 CPU provider 并记录模型哈希 |
| [sherpa-onnx Silero VAD](https://k2-fsa.github.io/sherpa/onnx/vad/silero-vad.html) / [字幕示例](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/generate-subtitles.py) | VAD 配置与长音频分段识别参考 | 固定 VAD 资产；结果仍发布到 VtNote 统一字幕结构，不引入多人识别 |
| [Refero Styles](https://styles.refero.design/) | 视觉风格样本 | 首屏采用暖中性、细边框和单一强调色；不复制营销页面 |
| [Vibe Interaction Glossary](https://vibe-hub.org/) | 前端交互动效术语与可视示例 | 用于统一状态反馈描述，不把效果本身变成功能 |
| [GSAP](https://gsap.com/) / [Showcase](https://gsap.com/showcase/) | 动效实现 API 与案例 | 固定 `gsap@3.15.0` 的 npm 依赖；仅用于功能状态且支持减少动态效果 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 平台 extractor 和字幕/媒体信息 | 通过固定版本 adapter 使用；继续保留 URL、DNS、redirect 和运行时外层限制 |
| [FFmpeg Legal](https://ffmpeg.org/legal.html) | LGPL/GPL 构建差异 | 实际发行必须检查 build configuration；开发环境的 GPL 构建不自动成为发行候选 |
| [BiliNote](https://github.com/JefferyHcool/BiliNote) | 开源视频到 AI 笔记产品参考 | 参考来源定位、结构化笔记；不把其全部功能纳入当前范围 |
| [BibiGPT Skill](https://github.com/JimmyLv/bibigpt-skill) | 字幕、总结、章节和 agent 接入产品参考 | 字幕-only 是独立意图；MCP/远程服务不是当前本地架构 |
| [NotebookLM Help](https://support.google.com/notebooklm/answer/16164461) | 多来源、grounded chat 和派生产物参考 | 参考“来源与派生产物分离”；不复制其云端数据模型 |
| [MacWhisper](https://www.macwhisper.com/) / [Batch Transcription](https://docs.macwhisper.com/article/19-batch-transcription) | 批量导入、检索、同步回听和速度控制参考 | 采用一文件一任务的多文件导入，以及详情页内的紧凑检索与回听；不增加独立媒体库 |
| [Buzz](https://github.com/chidiwilliams/buzz) | 本地转写、批处理和转写查看器参考 | 采用本地优先场景下的检索/播放闭环；不照搬桌面窗口和全部高级能力 |
| [Descript Search in script](https://help.descript.com/hc/en-us/articles/10164807821581-Search-in-script-tool) | 全稿检索与结果跳转参考 | 检索覆盖完整字幕并跳转分页；不引入非线性媒体编辑能力 |
| [DownKyi releases](https://github.com/leiurayer/downkyi/releases) | 本地 1.6.1 快照身份旁证 | 上游 release 页面与本地版本一致；源码复用仍受 GPL-3.0 约束 |

## DownKyi 本地证据

| 主题 | 路径 |
|---|---|
| 项目版本与日期 | 本地源码快照的 `CHANGELOG.md`、`src\DownKyi\Properties\AssemblyInfo.cs` |
| WPF/.NET/依赖 | 两个 `.csproj`、`src\DownKyi.Core\packages.config` |
| 项目许可 | 本地源码快照的 `LICENSE`（GPL-3.0 全文） |
| 字幕与 B 站数据流 | `DownKyi.Core\BiliApi\VideoStream\VideoStream.cs`、`DownKyi\Services\Download\DownloadService.cs` |
| aria2 | `DownKyi.Core\Aria2cNet\`、`aria2_COPYING.txt` |
| FFmpeg | `DownKyi.Core\FFmpeg\FFmpegHelper.cs`、`FFmpeg_LICENSE.txt`、发行 `ffmpeg.exe` 自报 buildconf |
| 存储 | `DownKyi.Core\Storage\`、发行目录的 `Storage/Config/aria/logs` |
| About 第三方表 | `DownKyi\Views\Settings\ViewAbout.xaml` |

本地发行目录含下载数据库、设置、日志、缓存和登录相关文件，只进行结构/哈希审阅，不读取或复制用户凭据内容，也不纳入 VtNote 仓库。

## 刷新触发器

出现以下情况时更新本文件和相关决策：

- 升级 yt-dlp、EJS/Deno、FFmpeg、faster-whisper、CTranslate2、sherpa-onnx、GSAP 或任一模型/VAD revision；
- 腾讯云接口、模型、地域、大小/时长、结果保留或计费合同变化；
- 当前仍隐藏的翻译或百炼能力准备进入 UI；
- 采用 DownKyi 或其他 GPL 项目的任何代码/二进制；
- 生成正式安装包、选择项目 LICENSE 或改变目标操作系统。
