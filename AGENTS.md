# VtNote Agent Guide

本文件适用于整个 `VtNote` 仓库。目标是让后续开发代理基于当前代码和本机事实继续工作，避免重复猜测、覆盖用户改动或误触发付费服务。

## 1. 开始工作前

1. 先运行 `git status --short`。当前工作树可能包含用户或其他代理尚未提交的改动；不要重置、覆盖或顺手格式化无关文件。
2. 阅读 `README.md`、`docs/README.md` 和与任务直接相关的现行文档。若文档与代码冲突，以当前代码、测试和实机检查为准，并同步修正文档。
3. 不确定外部服务参数、页面交互或返回格式时，使用官方文档或真实已登录页面核实；不要凭记忆猜字段。
4. 修改前确定需求边界。诊断请求只调查和说明；只有用户要求开发或修复时才改代码。

## 2. 本机开发环境

- 仓库：`D:\Workspace\Project\VtNote`
- Conda 环境：`vtnote`
- Python：`3.11.15`
- Python 可执行文件：`D:\ProgramData\Anaconda3\envs\vtnote\python.exe`
- Node.js：`24.12.0`
- npm：`11.6.2`
- FFmpeg / FFprobe：`vtnote` Conda 环境内 `7.1.1`，不保证在系统全局 PATH 中。
- `vtnote` 已以 editable 模式指向当前仓库。

所有 Python、pytest、FFmpeg 和正式启动命令都应在 `conda activate vtnote` 后执行。若自动化终端无法激活环境，使用：

```powershell
conda run -n vtnote python ...
conda run -n vtnote ffmpeg ...
```

非必要临时文件写入 `D:\Workspace\Codex\cache\VtNote-*`，不要在仓库或 C 盘留下测试缓存。

## 3. 数据目录

代码默认使用：

```text
%LOCALAPPDATA%\VtNote\Data
%LOCALAPPDATA%\VtNote\Cache
```

本机默认目录中已有数据库和任务记录。不要为了测试清空、迁移或覆盖它们。

需要隔离测试时，显式设置一对同盘路径：

```powershell
$env:VTNOTE_DATA_ROOT = 'D:\Workspace\Codex\cache\VtNote-test-data'
$env:VTNOTE_RUNTIME_CACHE_ROOT = 'D:\Workspace\Codex\cache\VtNote-test-runtime'
```

正式 D 盘模式约定为：

```text
D:\Workspace\Project\VtNote-data
D:\Workspace\Codex\cache\VtNote-runtime
```

不要在未获用户许可时把现有 C 盘数据迁移到 D 盘。`Settings` 要求两个根目录为绝对路径、互不包含，并位于同一 Windows 盘符。

## 4. 当前产品边界

- 前端主入口支持 Bilibili 链接和本地音视频上传。
- 后端也实现了受控 YouTube 适配器；只有固定 yt-dlp、EJS、Deno 和 D 盘运行时校验全部通过时才启用。
- 字幕顺序：平台字幕优先；否则进入 ASR。
- 云 ASR：腾讯云录音文件识别。
- 本地 ASR：`large-v3-turbo`、CUDA、`int8_float16`。当前没有 CPU fallback，不得在文档或 UI 中声称已经支持。
- AI 笔记：腾讯云 TokenHub，前端当前固定 `glm-5.1`。
- 翻译代码仍存在，但新建任务界面目前固定关闭翻译；不要把它描述为已开放的用户功能。
- 不支持登录态 Cookie、会员或付费视频、DRM、硬字幕 OCR。
- 所有模型和 ASR 调用暂定只使用国内服务；不要擅自增加国外 API 或第三方中转。

## 5. 架构约束

- `src/vtnote/launcher.py` 同时监督 API 与独立 Worker；不要用 FastAPI `BackgroundTasks` 或进程内队列替代耐久任务机制。
- API 只允许绑定 `127.0.0.1`；不要扩大到局域网地址。
- SQLite 保存任务、阶段、租约和恢复状态；文件产物必须经 `StoragePaths` 构造路径。
- `transcript.json` 是原文标准数据。SRT、TXT 等应从标准数据稳定生成，不要把导出文件作为新的真相来源。
- 用户原始本地文件永不删除。临时媒体只能位于 VtNote 管理的运行缓存中，并遵守回收与恢复语义。
- 写关键产物时保持原子发布和可恢复性；不要直接覆盖已完成的原文。
- 来源适配器保持平台隔离，继续执行 URL 白名单、重定向复检和资源主机限制。

## 6. 密钥与外部调用

- 密钥只能通过 `KeyringSecretStore` 进入 Windows Credential Manager；数据库、API、日志、任务快照、测试和文档中不得出现明文或可逆掩码。
- 日志与错误信息必须经过敏感文本保护；不要把上游原始响应整段永久保存。
- 腾讯云 ASR 验证会真实识别内置短音频，TokenHub 验证会真实调用模型；两者都可能消耗额度。
- 默认自动化测试不得触发真实付费请求、真实模型下载或大文件下载。任何这类操作都必须在用户明确授权后进行，并清楚说明目标服务和可能费用。
- 不读取或转发浏览器 Cookie。公开视频失败时应给出可操作错误，不得绕过平台限制。

## 7. 前端规则

- 前端源码在 `frontend/src`；生产服务读取 `frontend/dist`。
- 修改前端后至少运行测试和构建，确保正式 `vtnote` 启动能看到最新页面。
- 保持当前极简信息架构：新建处理、内容库、设置。不要自行增加统计卡、会员、账号体系、可视化工作流或多余说明文字。
- 点击“开始处理”后进入内容库，由内容库行内进度展示状态；不要重新引入独立的处理中间页。
- 交互必须保留键盘可达性、可读状态文本和合理的 `aria-*` 语义。
- UI 文案优先简洁中文；错误信息应能指导用户操作，避免直接显示内部异常名作为唯一说明。

## 8. 后端规则

- 使用 Python 3.11、Pydantic 2、SQLAlchemy 2 和 FastAPI 现有模式。
- 新的任务状态或阶段状态必须同时更新 schema、数据库逻辑、API 类型、前端展示和测试。
- 阶段失败应保持已完成产物；AI 笔记失败不能破坏字幕，也不能重新下载或重新转写。
- 云请求错误要区分：鉴权/配置错误、限流/服务端错误、超时且结果未知。未知结果禁止盲目重复付费提交。
- 本地 ASR 模型必须由受控模型资产服务安装和校验；运行时不得临时从互联网自动拉取未登记模型。

## 9. 验证命令

按改动影响面运行最小充分测试。

文档修改：

```powershell
git diff --check -- README.md AGENTS.md docs
```

后端定向测试：

```powershell
conda run -n vtnote python -m pytest -q tests\test_target.py
```

后端完整测试：

```powershell
conda run -n vtnote python -m pytest -q
```

前端：

```powershell
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

启动检查：

```powershell
conda activate vtnote
vtnote
Invoke-RestMethod http://127.0.0.1:8765/api/health
Invoke-RestMethod http://127.0.0.1:8765/api/readiness
```

不要为了文档或局部 UI 修改反复运行真实 ASR/AI 端到端测试。

## 10. Git 与交接

- 只提交本任务相关文件；不要把现有脏工作树中的其他改动一起纳入提交。
- 不提交 `frontend/dist`、`node_modules`、数据库、用户数据、缓存、模型、日志、密钥或测试下载物。
- 不使用 `git reset --hard`、`git checkout --` 等破坏性命令处理用户改动。
- 交接时说明：修改文件、验证命令、未运行的真实外部测试、已知限制。
- 用户要求推送时使用已安装的 GitHub 插件工作流；推送前再次核对 diff 和分支。当前本地分支是 `feature/vtnote-v1`，不要自行合并到 `main`。
