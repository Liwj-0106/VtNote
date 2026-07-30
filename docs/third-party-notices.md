# 第三方组件与许可说明

本文件是开发清单，不替代法律意见。发布前必须以实际打包内容重新生成证据并核对
许可证正文。

| 组件 | 固定版本 | 许可/注意事项 | 上游 |
|---|---:|---|---|
| Python | 3.11.15 | PSF License | https://www.python.org/ |
| FastAPI | 0.115.14 | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | 0.34.3 | BSD-3-Clause | https://github.com/encode/uvicorn |
| SQLAlchemy | 2.0.51 | MIT | https://github.com/sqlalchemy/sqlalchemy |
| HTTPX | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx |
| yt-dlp | 2026.7.4 | Unlicense；某些构建可能附带 ISC/MIT 组件 | https://github.com/yt-dlp/yt-dlp |
| yt-dlp-ejs | 0.8.0 | Unlicense；wheel 内含 ISC `meriyah` 与 MIT `astring` | https://github.com/yt-dlp/ejs |
| Deno | 2.8.1 | MIT | https://github.com/denoland/deno |
| faster-whisper | 1.2.1 | MIT | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | 4.8.1 | MIT | https://github.com/OpenNMT/CTranslate2 |
| Tencent COS Python SDK | 1.9.44 | 安装元数据标记 MIT | https://github.com/tencentyun/cos-python-sdk-v5 |
| React / React DOM | 19.1.1 | MIT | https://github.com/facebook/react |
| Vite | 7.3.6 | MIT | https://github.com/vitejs/vite |
| TypeScript | 5.9.2 | Apache-2.0 | https://github.com/microsoft/TypeScript |
| Whisper large-v3-turbo 权重 | 固定 manifest revision | 发布前核对模型卡与权重许可 | https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo |
| NVIDIA CUDA/cuBLAS/cuDNN | 12.8/12.8/9.10 | 受 NVIDIA 相应许可约束 | https://docs.nvidia.com/cuda/eula/ |

FFmpeg 本身可按 LGPL 2.1+ 使用，但启用 GPL 组件会使组合构建受 GPL 约束。当前
Conda 开发构建包含 `--enable-gpl`，因此发布工具会将它标记为
`development_gpl_only`。正式分发必须换成经验证的 LGPL 候选，或明确采用并履行
GPL 义务。参考：https://ffmpeg.org/legal.html
