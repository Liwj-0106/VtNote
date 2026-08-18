# VtNote 安装、启动与备份

VtNote 当前支持 Windows 10/11。本地进程只监听 `127.0.0.1:8765`，由一个
supervisor 同时管理 FastAPI 和独立 Worker。

## 环境要求

- Conda；Python 固定为 3.11。
- Node.js 24 与 npm。
- NVIDIA GPU（本地 faster-whisper 路径仅支持 CUDA）。
- FFmpeg/FFprobe；开发环境由 `environment.yml` 固定。

## 安装

```powershell
cd D:\Workspace\Project\VtNote
conda env create -f environment.yml
conda activate vtnote
python -m pip install --requirement requirements.lock
python -m pip install --no-deps --editable .

cd frontend
npm ci
npm run build
cd ..
```

环境已存在时使用 `conda env update -f environment.yml --prune`，再重复 pip 安装和
前端构建。不要在运行时自动下载未登记的 Python、Deno 或模型组件。

## 启动与停止

```powershell
conda activate vtnote
vtnote
```

浏览器打开 `http://127.0.0.1:8765`。在启动终端按 `Ctrl+C`，supervisor 会依次停止
API 和 Worker。

只读健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
Invoke-RestMethod http://127.0.0.1:8765/api/readiness
```

## 首次配置

1. 在“设置 → 管理 ASR”添加腾讯云录音文件识别凭据并验证。
2. 较长音频若无法内联，需要配置私有 COS；应用只使用受控临时对象并在终态清理。
3. 要生成 AI 笔记时，在“管理模型”添加并验证 TokenHub 连接；当前前端使用
   `glm-5.1`。
4. 本地 ASR 需在模型资产页面安装并校验 `large-v3-turbo`。未安装时不会把它当成
   可用回退。

连接验证会真实调用服务，可能产生少量用量。密钥保存于 Windows Credential
Manager，不要写入源码、`.env`、文档或启动脚本。

## 数据目录

默认位置：

```text
%LOCALAPPDATA%\VtNote\Data
%LOCALAPPDATA%\VtNote\Cache
```

可用两个绝对路径环境变量覆盖：

```powershell
$env:VTNOTE_DATA_ROOT = 'D:\VtNote\Data'
$env:VTNOTE_RUNTIME_CACHE_ROOT = 'D:\VtNote\Cache'
```

两个根目录必须互不包含，并位于同一 Windows 盘符。目录内容和清理边界见
[技术方案](technical-decisions.md#数据与存储)。

## 备份与迁移

1. 停止 VtNote。
2. 备份完整 `Data`；若任务仍在运行或需要保留音频导出，也同时备份 `Cache`。
3. Windows Credential Manager 中的云凭据不在文件备份内，需要单独重新配置。
4. 迁移后保持两个根目录和数据库中文件引用一致，再启动应用检查内容库。

`Cache` 包含可恢复任务的中间媒体和按需生成的音频导出，并非任何时候都能安全
整目录删除。只使用应用内删除/回收机制，或在确认没有活动任务且不需要缓存产物后
人工清理。

DownKyi 与 VtNote 没有运行时集成。可把 DownKyi 单独下载得到的本地音视频作为普通
本地文件导入 VtNote；VtNote 不读取其 Cookie、SQLite、aria2 会话或程序目录。
