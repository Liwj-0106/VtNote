# 第三方组件与许可说明

本页记录当前源码直接依赖和发行检查规则，不替代由实际产物生成的 SBOM，也不构成
法律意见。版本以 `pyproject.toml`、`requirements.lock`、`environment.yml` 和
`frontend/package-lock.json` 为准。

## 直接运行时依赖

| 组件 | 当前版本 | 许可标识/注意事项 |
|---|---:|---|
| Python | 3.11.15 | PSF-2.0 |
| FastAPI | 0.115.14 | MIT |
| Uvicorn | 0.34.3 | BSD-3-Clause |
| SQLAlchemy | 2.0.51 | MIT |
| HTTPX | 0.28.1 | BSD-3-Clause |
| Pydantic | 2.13.4 | MIT |
| keyring | 25.7.0 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| yt-dlp | 2026.7.4 | Unlicense；实际包还需核对所含组件 |
| yt-dlp-ejs | 0.8.0 | Unlicense；包内另含 MIT/ISC 组件 |
| faster-whisper | 1.2.1 | MIT |
| CTranslate2 | 4.8.1 | MIT |
| Tencent COS Python SDK | 1.9.44 | 上游元数据为 MIT |
| React / React DOM | 19.1.1 | MIT |
| Vite | 7.3.6 | MIT |
| TypeScript | 5.9.2 | Apache-2.0 |

Deno、CUDA/cuBLAS/cuDNN、模型权重和 FFmpeg 是否构成发行内容，必须以最终安装包为
准并单独核对再分发条款。开发与测试工具（Vitest、Playwright、pytest 等）不应自动
被当成发行依赖。

## FFmpeg

当前 Conda 开发环境中的 FFmpeg 7.1.1 启用了 `--enable-gpl`，不能标成 LGPL
发行候选。正式发布必须二选一：

- 换用已记录版本、哈希和 `-buildconf` 的 LGPL 兼容构建；或
- 明确采用 GPL 构建，并履行该构建和所含编解码器的全部义务。

发布证据由 `tools/collect_release_evidence.mjs` 采集；判定仍需人工复核
[FFmpeg 官方许可说明](https://ffmpeg.org/legal.html)。

## 模型与硬件运行时

- `large-v3-turbo` 的模型卡、权重来源、固定 revision 与许可必须在发布前冻结。
- NVIDIA 运行时受各自 EULA 约束，不能因 Conda 可安装就推定可随应用重分发。
- 可选运行时组件若在用户机器上独立安装，也应在安装说明中明确来源和版本边界。

## DownKyi 参考项目

本项目当前没有复制或打包 DownKyi、aria2、DownKyi 的 FFmpeg、Prism、WebPSharp 等
组件；它们是本地调研对象，不属于 VtNote 第三方依赖。内部效果实验与正式合并/分发
分开管理；若未来复制 GPL 代码或捆绑 GPL 二进制，必须把来源、修改和对应源码义务
纳入发布门禁。详见[参考项目调研](reference-projects.md)。

## 发布门禁

1. 当前仓库没有项目自身的 `LICENSE`，这是对外分发前的阻断项。
2. 从最终 wheel、npm、Conda 与二进制产物生成完整依赖清单、版本、SPDX、上游地址、
   哈希和许可证正文；本页的手工表格不能替代它。
3. 检查传递依赖、原生库和可选下载组件，而不只检查直接依赖。
4. About/许可页面当前延期；未来若实现，应由构建清单生成，避免像参考项目一样遗漏组件。
