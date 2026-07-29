# VtNote 安装与启动

## 1. 环境位置

- 项目：`D:\Workspace\Project\VtNote`
- Conda 环境：建议使用 `vtnote`
- 长期数据：`D:\Workspace\Project\VtNote-data`
- 临时文件、日志和运行时：`D:\Workspace\Codex\cache\VtNote-runtime`

不要把密钥写入 `.env`、数据库或启动脚本；界面会把腾讯云和阿里云密钥保存到
Windows Credential Manager，并且 API 只返回 `has_secret`。

## 2. 创建环境

```powershell
cd D:\Workspace\Project\VtNote
conda env create -f environment.yml
conda activate vtnote
python -m pip install --requirement requirements.lock
python -m pip install --no-deps --editable .
```

`environment.yml` 固定 Python、FFmpeg、CUDA、cuBLAS 和 cuDNN；`requirements.lock`
固定 Python 包。不要用系统 Python 替代项目 Conda 环境。

## 3. 构建前端

需要 Node.js 24：

```powershell
cd D:\Workspace\Project\VtNote\frontend
npm ci
npm run build
```

生产模式只提供 `frontend\dist` 中的本地静态文件，不加载第三方字体、脚本或 CDN。

## 4. 启用 YouTube

VtNote 固定使用 `yt-dlp 2026.7.4`、`yt-dlp-ejs 0.8.0` 和 Deno 2.8.1，
并禁止远程 EJS、系统 Node 回退和自动升级。

1. 从 Deno 官方发布页下载
   `deno-x86_64-pc-windows-msvc.zip` 2.8.1。
2. 执行固定哈希安装：

```powershell
python tools\install_youtube_runtime.py `
  --archive D:\Downloads\deno-x86_64-pc-windows-msvc.zip `
  --acknowledge-install
```

安装器验证归档 SHA-256
`5fb5bac71f609fb91ec8960fb290885aadc27eeb22f07a8eca0c3db6be38b11a`
和 `deno.exe` SHA-256
`a8afddac131261dc9e085c6a1a79544f0567bd09e481034b5d1533588cba9b30`，
只写入 D 盘运行时目录。腾讯云、百炼和模型文件仍需在界面中单独配置或下载。

## 5. 启动

```powershell
conda activate vtnote
vtnote
```

启动器只监听 `127.0.0.1:8765`，监督 API 和 worker，并在退出时清理子进程。
浏览器打开 `http://127.0.0.1:8765`。若端口被占用，启动器会直接报告
`port_unavailable`，不会改用公网地址或随机端口。

## 6. 备份、升级与移除

- 备份：停止 VtNote 后复制整个 `VtNote-data`；它包含 SQLite、原始字幕、
  `transcript.json`、译文和笔记，但不包含 Credential Manager 中的密钥。
- 升级：备份数据，更新代码，在原 Conda 环境重新安装锁文件，重新运行
  `npm ci && npm run build`，然后查看“设置 → 运行环境”。
- 恢复：停止程序，用完整备份替换数据目录，再启动；不要只复制 `vtnote.db`。
- 移除：先导出所需结果，再删除 Conda 环境、项目数据目录和 D 盘运行时目录；
  最后在 Windows Credential Manager 中删除 VtNote 相关凭据。
