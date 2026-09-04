# VtNote

把视频里的知识变成可检索、可复用的文档。

VtNote 是一个通过浏览器使用的音视频字幕与总结工具，支持 B 站、YouTube、抖音等视频链接，也支持本地音视频和字幕文件。它把转写、总结、章节整理和长期归档放进同一条流程；默认只监听 `127.0.0.1`，内容库与处理成果保存在本机。

## 主要能力

- 从单个链接、本地文件或识别出的 B 站合集创建处理任务。
- 生成并查看全文总结、按章节组织的原文、时间戳字幕和可选译文。
- 点击原文或字幕句段，将左侧在线视频定位到对应时间。
- 在总结记录中搜索、筛选、批量选择、导出，并用合集整理内容。
- 按结果类型导出 Markdown、TXT、SRT、VTT、LRC、JSON，以及可用的音频格式。
- 本地 ASR 可选择 SenseVoice Small INT8（sherpa-onnx + Silero VAD，CPU）或 Faster-Whisper；两者均由用户显式安装固定资产。

## 目录结构

| 目录/文件 | 用途 |
|---|---|
| `src/vtnote/` | Python 启动器、业务服务、Worker、流水线与打包资源解析 |
| `src/vtnote/http/` | HTTP 路由、请求合同和响应边界 |
| `src/vtnote/application/` | 不依赖持久层的跨入口应用合同 |
| `frontend/src/pages/` | 路由级数据加载与页面编排 |
| `frontend/src/features/` | 任务创建、内容库、合集、结果阅读等有状态业务能力 |
| `frontend/src/components/` | 弹窗、搜索、下拉菜单等无业务通用组件 |
| `frontend/src/styles/` | 全局基础样式和按功能拆分的有序样式入口 |
| `frontend/` | React/TypeScript 前端、Vitest/Playwright 测试与生产构建 |
| `assets/` | 模型清单和内置验证音频源文件 |
| `design-system/` | 当前界面设计基线和页面参考 |
| `tests/` | Python 单元、集成、架构与离线端到端测试 |
| `docs/` | 产品、技术、安装、界面、调研和发布基线文档 |
| `tools/` | 项目内安装、发布证据和打包工具 |
| `environment.yml` | Conda/FFmpeg/CUDA/Python 开发环境 |
| `pyproject.toml`、`requirements.lock` | Python 包与锁定依赖 |
| `frontend/package.json`、`frontend/package-lock.json` | 前端依赖与命令 |
| `MANIFEST.in`、`setup.py` | wheel 资源清单和兼容打包入口 |
| `.vtnote/Data/` | 当前源码工作区的内容库数据库和持久成果，已忽略 |
| `.vtnote/Cache/` | 当前源码工作区的运行、测试与打包缓存及日志，已忽略 |
| `.vtnote/ManagedAssets/` | 本地 ASR 模型、VAD 和 YouTube/Deno 运行时，已忽略 |
| `exports/` | 默认导出目录，已忽略；可在设置中改为其他现有目录 |
| `dist/` | 已忽略的 wheel 和发布产物 |

## 架构与复用边界

API 与 Worker 共享耐久任务合同，但分别负责请求编排和短事务执行；重试校验、阶段模型判定、结果产物读取等纯规则位于独立模块，避免在两个入口重复实现。`api.py`、`tasks.py` 和 `configuration.py` 保留稳定导入路径，新增职责按 HTTP、应用合同或具体业务模块归档。

前端页面只组合功能模块。任务创建参数、总结设置和模型可用性由 `features/` 中的共享模型统一计算；原生弹窗行为由 `ModalDialog` 统一处理；总结记录的选择状态和记录展示分别由 hook 与工作区组件承载。分段标签、浮层定位和进出场分别复用 `SegmentedTabs`、共享菜单组件和 `MotionPresence`，避免页面重复实现键盘导航、碰撞处理与动效。大型功能样式使用同名 CSS 入口按顺序导入作用域分片，避免页面、弹窗和响应式规则继续堆积在单个文件。

界面基线以 [VtNote 设计规范](design-system/vtnote/MASTER.md) 为准。主题色、层级、控件尺寸和动效时长集中在 `frontend/src/styles/tokens.css`；新增页面应复用这些语义令牌并支持减少动态效果偏好，不在页面内另造配色或装饰性动画。

这些边界由 `tests/test_architecture_boundaries.py` 持续检查。新增能力应优先扩展现有共享模型或功能模块，再由页面接入。

## 安装与运行

前置条件：Conda、Node.js/npm。Python、FFmpeg、CUDA 和 Python 包版本由 `environment.yml` 声明；前端版本由 lockfile 声明。

```powershell
conda env create -f environment.yml
conda activate vtnote
npm --prefix frontend ci
npm --prefix frontend run build
python -m vtnote
```

浏览器打开 `http://127.0.0.1:8766`。首次使用按页面引导配置转写和 AI 模型；不使用 AI 总结时仍可处理本地转写能力支持的内容。固定端口登记见工作区的 [本地端口登记表](../../docs/local-ports.md)，完整配置与故障检查见 [安装与运维](docs/installation.md)。

从源码工作区启动时，入口会根据项目自身位置把 Data、Cache 和 ManagedAssets 分别定位到 `.vtnote/` 下，不依赖调用者当前目录或个人绝对路径。源码入口只接受 `.vtnote/` 内的隔离存储覆盖，旧的外部存储环境变量不会重新创建项目外数据目录。`.vtnote/` 已被 Git 忽略；可选代理和 Cookie 仍需由用户显式配置，安装与构建不依赖这些运行数据。

## 测试

应用测试默认使用本地夹具和隔离存储，不调用真实 ASR、AI 或视频平台服务。按影响面选择以下命令：

```powershell
conda activate vtnote
python -m pytest -q
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test:e2e:install
npm --prefix frontend run test:e2e
```

`test:e2e:install` 首次运行可能联网下载 Chromium，浏览器写入 `.vtnote/Cache/test/playwright/`；后续 E2E 测试仍使用本机 API、Vite 服务和隔离数据。

启动后可以检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/health
Invoke-RestMethod http://127.0.0.1:8766/api/readiness
```

## 打包与安装

项目提供单命令 wheel 打包入口：

```powershell
conda activate vtnote
python tools/package.py
```

产物写入 `dist/`。打包过程会构建前端，并验证 wheel 已包含生产页面、模型清单和内置验证音频，同时拒绝包含个人 Windows 绝对路径的文本文件。

安装打包结果：

```powershell
python -m pip install --no-deps dist/vtnote-0.1.0-py3-none-any.whl
vtnote
```

wheel 安装后不依赖仓库中的 `frontend/dist`、`assets/`、个人目录或本机缓存。实际运行仍需要已声明的 Python 依赖和 FFmpeg；SenseVoice 使用 CPU，Faster-Whisper 的 GPU 配置需要 `environment.yml` 中对应的 CUDA 运行时。
