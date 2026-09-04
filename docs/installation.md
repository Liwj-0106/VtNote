# VtNote 安装、启动与备份

VtNote 当前支持 Windows 10/11。本地进程只监听 `127.0.0.1:8766`，由一个
supervisor 同时管理 FastAPI 和独立 Worker。

## 环境要求

- Conda；Python 固定为 3.11。
- Node.js 24 与 npm。
- SenseVoice Small INT8 使用 CPU；Faster-Whisper 首选 NVIDIA GPU，也可在设置中显式开启 CPU 降级。
- FFmpeg/FFprobe；开发环境由 `environment.yml` 固定。

## 安装

```powershell
conda env create -f environment.yml
conda activate vtnote
npm --prefix frontend ci
npm --prefix frontend run build
```

环境已存在时使用 `conda env update -f environment.yml --prune`，再重复 pip 安装和
前端构建。不要在运行时自动下载未登记的 Python、Deno 或模型组件。

## 启动与停止

```powershell
conda activate vtnote
vtnote
```

浏览器打开 `http://127.0.0.1:8766`。在启动终端按 `Ctrl+C`，supervisor 会依次停止
API 和 Worker。

只读健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/health
Invoke-RestMethod http://127.0.0.1:8766/api/readiness
```

## 首次配置

1. 在“设置 → 管理 ASR”添加腾讯云录音文件识别凭据并验证。
2. 较长音频若无法内联，需要配置私有 COS；应用只使用受控临时对象并在终态清理。
3. 要生成 AI 笔记时，在“管理模型”添加并验证 TokenHub 连接；当前前端使用
   `glm-5.1`。
4. 本地 ASR 在“设置 → 模型”中选择。SenseVoice 需要安装并校验固定的 INT8 模型与
   Silero VAD；Faster-Whisper 需要安装并校验 `large-v3-turbo`。资产未就绪时不会把
   对应引擎当成可用回退。

云端连接验证会真实调用服务，可能产生少量用量。密钥保存于 Windows Credential
Manager，不要写入源码、`.env`、文档或启动脚本。

## 数据目录

源码工作区只有一套运行目录：

```text
.vtnote\Data
.vtnote\Cache
.vtnote\ManagedAssets
```

入口根据仓库自身位置解析这些目录，不依赖当前工作目录。测试可以通过
`VTNOTE_DATA_ROOT`、`VTNOTE_RUNTIME_CACHE_ROOT` 和
`VTNOTE_MANAGED_ASSETS_ROOT` 选择 `.vtnote` 内的隔离子目录；源码入口会拒绝项目外
根路径，避免移动仓库后由旧环境变量重新生成外部数据目录。

`ManagedAssets\Data` 保存已校验的本地 ASR 模型与 VAD，`ManagedAssets\Cache` 保存
模型安装暂存和固定版本 Deno。它们与内容库 Data/Cache 分离，但仍由同一项目拥有。

若 YouTube 或固定模型资产在当前网络只能通过本机代理访问，可显式配置回环
HTTP CONNECT 代理；平台请求与模型安装器复用同一项配置：

```powershell
$env:VTNOTE_PLATFORM_PROXY_URL = 'http://127.0.0.1:<端口>'
```

只允许无用户名/密码、无路径的 `127.0.0.1` 或 `[::1]` 地址。该设置不会读取代理
凭据；目标 URL、重定向、请求体大小和 TLS 主机名仍由 VtNote 校验。未配置时使用
DNS 固定直连。

在用户明确授权后，可为抖音和 YouTube 分别加载 Chrome 导出的 Netscape
Cookie 文件：

```powershell
$env:VTNOTE_PLATFORM_DOUYIN_COOKIE_FILE = '<仓库外绝对路径>\douyin-cookies.txt'
$env:VTNOTE_PLATFORM_YOUTUBE_COOKIE_FILE = '<仓库外绝对路径>\youtube-cookies.txt'
```

每个文件按对应平台域名过滤，只驻留 API/Worker 内存，不写入数据库、日志或任务快照，
且文件必须保存在仓库外。`VTNOTE_PLATFORM_COOKIE_BROWSER=firefox|chrome|edge` 仍可作为
直接浏览器导入备选，文件配置优先。Windows 新版 Chrome/Edge 的 App-Bound `v20`
可能无法由 yt-dlp 解密，此时使用 Chrome 本地导出的文件或 Firefox。导入失败只禁用
对应平台登录态能力，不影响本地文件和匿名链接。该能力只用于用户有权访问的公开单
视频，不改变会员内容、DRM 和直播边界。

Data、Cache 和 ManagedAssets 必须互不包含。目录内容和清理边界见
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

## 打包

```powershell
conda activate vtnote
python tools/package.py
```

产物位于项目 `dist/`。wheel 内包含生产前端、模型清单和内置验证音频。打包与 wheel
安装冒烟检查会在这些资源缺失时失败；安装后的 VtNote 不再读取源码仓库中的
`frontend/dist`、`assets` 或根目录环境文件。
