# VtNote

VtNote 是一个本地优先的音视频字幕与笔记工具。用户可以粘贴公开 B 站链接或上传本地音视频，按需生成音频、字幕原文和 AI 笔记，再从内容库导出结果。

当前基线以 `feature/vtnote-v1` 工作树为准，主要面向 Windows 10/11。应用只监听 `127.0.0.1:8765`，没有账号系统，也不会把服务暴露到局域网。

## 当前能力

- 输入：公开、无需登录的 Bilibili 单视频链接；本地音视频文件。
- 字幕：平台字幕优先；没有可用字幕时进入腾讯云录音文件识别，必要时回退到已安装的本地 `faster-whisper`。
- 笔记：仅在用户选择“AI 笔记”且配置已验证模型时调用腾讯云 TokenHub；只选择字幕不会调用大模型。
- 导出：M4A/MP3 音频、SRT/TXT 字幕、Markdown/TXT 笔记。
- 失败任务：仍可导出已经完成的成果，未生成的结果会保持禁用。
- 任务执行：独立 Worker、SQLite 持久队列、阶段租约、崩溃恢复和外部请求防重复。

当前前端没有开放 YouTube、本地字幕直传、翻译、批量处理或登录态 Cookie。后端保留部分实验性/兼容代码，但不构成当前产品承诺。也不支持会员内容、DRM 或硬字幕 OCR。

## 技术架构

- 前端：React 19、TypeScript 5.9、Vite 7。
- API：Python 3.11、FastAPI、Uvicorn。
- 持久化：SQLAlchemy 2 + SQLite WAL。
- 任务：API 与独立 Worker 通过 SQLite 和规范文件产物协作。
- 媒体：FFmpeg/FFprobe；平台解析使用固定版本 yt-dlp。
- ASR：腾讯云录音文件识别；可选 `faster-whisper + CTranslate2 + CUDA`。
- AI：当前 UI 使用腾讯云 TokenHub `glm-5.1`；阿里云百炼仅为后端遗留兼容路径。
- 密钥：Windows Credential Manager；自定义提示词使用当前用户 DPAPI 保护。

完整组件关系、数据流和技术决策见 [技术方案](docs/technical-decisions.md)。

## 快速启动

项目要求 Python `>=3.11,<3.12`。本机推荐使用 Conda 环境：

```powershell
cd D:\Workspace\Project\VtNote
conda env create -f environment.yml
conda activate vtnote

npm --prefix frontend ci
npm --prefix frontend run build
python -m pip install --no-deps --editable .
python -m vtnote
```

浏览器打开 `http://127.0.0.1:8765`。已有环境可以直接：

```powershell
conda activate vtnote
cd D:\Workspace\Project\VtNote
python -m vtnote
```

详细安装、配置、备份和迁移方法见 [安装与运维](docs/installation.md)。

## 数据保存位置

Windows 默认目录：

```text
%LOCALAPPDATA%\VtNote\Data
%LOCALAPPDATA%\VtNote\Cache
```

- `Data` 保存 SQLite、规范字幕、译文、笔记、恢复产物和已安装模型，属于长期数据。
- `Cache` 保存上传副本、下载音频、转码文件、音频导出、日志和回收站。部分 active 媒体仍是内容库成果，不能在应用运行时直接清空。
- 腾讯云和 TokenHub 密钥不在上述目录或数据库中，而在 Windows Credential Manager。

可以通过 `VTNOTE_DATA_ROOT` 和 `VTNOTE_RUNTIME_CACHE_ROOT` 修改目录。两者必须是绝对路径、互不包含；Windows 上必须位于同一盘符。完整数据清单和清理边界见 [技术方案：数据与存储](docs/technical-decisions.md#数据与存储)。

## 验证

默认测试全部离线，不会调用真实 ASR、大模型或下载模型：

```powershell
conda activate vtnote
python -m pytest -q
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

应用启动后：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
Invoke-RestMethod http://127.0.0.1:8765/api/readiness
```

测试分层和发布门禁见 [测试与发布](docs/release-checklist.md)。

## 文档

- [产品需求（PRD）](docs/product-requirements.md)
- [技术方案与决策](docs/technical-decisions.md)
- [产品与技术调研](docs/reference-projects.md)
- [界面与交互设计](docs/website-specification.md)
- [安装与运维](docs/installation.md)
- [第三方组件与许可](docs/third-party-notices.md)
- [人工验收](docs/VtNote-人工验收测试用例.md)

文档索引见 [docs/README.md](docs/README.md)。

## 安全边界

- 不要把密钥、Cookie、Token、数据库、用户媒体、模型或缓存提交到 Git。
- 不要在应用运行时复制 SQLite WAL 或手工删除其管理的 cache 文件。
- VtNote 不删除用户选择的原始本地文件，只管理自己的上传副本和运行资产。
- 仅处理用户有权访问和处理的内容。
- 仓库当前没有项目级 `LICENSE`；确定发行方式前必须先完成许可证选择和实际制品许可审计。
