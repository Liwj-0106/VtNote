# VtNote 调研来源

校准日期：2026-08-19

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
| 本地 ASR | `src/vtnote/model_assets.py`、`local_asr.py`、`assets/models/*.manifest.json` |
| TokenHub | `src/vtnote/tokenhub_chat.py`、`ai_stages.py`、`frontend/src/pages/AiConnectionsPage.tsx` |
| 密钥与提示词保护 | `src/vtnote/secrets.py`、`provider_credentials.py`、`sensitive_text.py` |
| 许可制品证据 | `tools/collect_release_evidence.mjs`、实际 `ffmpeg -version/-buildconf` |

## 官方/上游来源

| 来源 | 用途 | 2026-08-19 采用结论 |
|---|---|---|
| [Tencent CreateRecTask](https://www.tencentcloud.com/document/product/1118/66925) | 录音文件识别提交、Base64/URL 与异步任务 | 采用异步任务边界；具体模型、大小和地域仍由本地固定合同与真实账户验证 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | CTranslate2 Whisper 推理、GPU/CPU/量化选项和 MIT 许可 | 当前固定 GPU profile；上游支持 CPU 不代表 VtNote 已开放 CPU fallback |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 平台 extractor 和字幕/媒体信息 | 通过固定版本 adapter 使用；继续保留 URL、DNS、redirect 和运行时外层限制 |
| [FFmpeg Legal](https://ffmpeg.org/legal.html) | LGPL/GPL 构建差异 | 实际发行必须检查 build configuration；开发环境的 GPL 构建不自动成为发行候选 |
| [BiliNote](https://github.com/JefferyHcool/BiliNote) | 开源视频到 AI 笔记产品参考 | 参考来源定位、结构化笔记；不把其全部功能纳入当前范围 |
| [BibiGPT Skill](https://github.com/JimmyLv/bibigpt-skill) | 字幕、总结、章节和 agent 接入产品参考 | 字幕-only 是独立意图；MCP/远程服务不是当前本地架构 |
| [NotebookLM Help](https://support.google.com/notebooklm/answer/16164461) | 多来源、grounded chat 和派生产物参考 | 参考“来源与派生产物分离”；不复制其云端数据模型 |
| [DownKyi releases](https://github.com/leiurayer/downkyi/releases) | 本地 1.6.1 快照身份旁证 | 上游 release 页面与本地版本一致；源码复用仍受 GPL-3.0 约束 |

## DownKyi 本地证据

| 主题 | 路径 |
|---|---|
| 项目版本与日期 | `D:\Workspace\Project\CY\downkyi--main\CHANGELOG.md`、`src\DownKyi\Properties\AssemblyInfo.cs` |
| WPF/.NET/依赖 | 两个 `.csproj`、`src\DownKyi.Core\packages.config` |
| 项目许可 | `D:\Workspace\Project\CY\downkyi--main\LICENSE`（GPL-3.0 全文） |
| 字幕与 B 站数据流 | `DownKyi.Core\BiliApi\VideoStream\VideoStream.cs`、`DownKyi\Services\Download\DownloadService.cs` |
| aria2 | `DownKyi.Core\Aria2cNet\`、`aria2_COPYING.txt` |
| FFmpeg | `DownKyi.Core\FFmpeg\FFmpegHelper.cs`、`FFmpeg_LICENSE.txt`、发行 `ffmpeg.exe` 自报 buildconf |
| 存储 | `DownKyi.Core\Storage\`、发行目录的 `Storage/Config/aria/logs` |
| About 第三方表 | `DownKyi\Views\Settings\ViewAbout.xaml` |

本地发行目录含下载数据库、设置、日志、缓存和登录相关文件，只进行结构/哈希审阅，不读取或复制用户凭据内容，也不纳入 VtNote 仓库。

## 刷新触发器

出现以下情况时更新本文件和相关决策：

- 升级 yt-dlp、EJS/Deno、FFmpeg、faster-whisper、CTranslate2 或模型 revision；
- 腾讯云接口、模型、地域、大小/时长、结果保留或计费合同变化；
- 当前隐藏的 YouTube、翻译、百炼或本地字幕能力准备进入 UI；
- 采用 DownKyi 或其他 GPL 项目的任何代码/二进制；
- 生成正式安装包、选择项目 LICENSE 或改变目标操作系统。
