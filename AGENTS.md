# VtNote 协作约束

本文件适用于整个 VtNote 项目。只记录明确、可执行且可长期维护的规则。

## 开始与边界

- 修改前读取根 `README.md`、相关现行文档、依赖清单，并运行 `git status --short`。
- 工作树可能包含用户未提交修改；不得重置、覆盖、清理或顺手格式化无关内容。
- 源码、配置、静态资源、测试、依赖清单、构建与打包入口必须保留在项目根目录内。
- 安装、构建、测试和打包不得依赖项目外脚本、个人绝对路径、预先存在的模型、Cookie、数据库或本机缓存。
- 当前源码工作区的持久数据、缓存、日志、模型和 YouTube/Deno 运行时分别写入已忽略的 `.vtnote/Data`、`.vtnote/Cache` 和 `.vtnote/ManagedAssets`；源码入口只接受 `.vtnote/` 内的隔离存储覆盖，不得再为本项目新建一级数据目录或依赖个人绝对路径。
- 不读取、输出或提交 `.env`、Cookie、密钥、数据库、用户媒体、模型文件和运行日志。

## 环境与依赖

- Python 使用项目 `environment.yml` 声明的独立 Conda 环境；所有 Python、pytest、FFmpeg 和启动命令均在该环境中执行。
- Node 依赖只使用 `frontend/package-lock.json`，安装命令为 `npm --prefix frontend ci`；不得生成第二份锁文件。
- Python 依赖以 `pyproject.toml`、`environment.yml` 和 `requirements.lock` 为准；修改依赖时同步更新相关清单。
- 测试默认离线，不得触发真实 ASR、AI、模型下载或平台大文件下载。付费或外部实测必须获得用户明确授权。

## 架构

- `src/vtnote/launcher.py` 监督 API 与独立 Worker；耐久任务继续使用 SQLite 队列、租约和恢复点。
- `src/vtnote/api.py`、`tasks.py`、`configuration.py` 只保留兼容门面与组合逻辑。新 HTTP 职责进入 `src/vtnote/http/`，跨入口合同进入 `src/vtnote/application/`，业务能力进入专用模块。
- 保留 `vtnote.api`、`vtnote.tasks`、`vtnote.configuration` 的兼容导入路径。
- API 与 Worker 共用的重试边界、阶段模型判定和结果产物规则分别维护在 `retry_policy.py`、`stage_models.py` 和 `result_artifacts.py`；不得在服务或 Worker 中复制实现。
- 前端页面只做路由级加载和编排；带独立状态或交互的能力进入 `frontend/src/features/<capability>/`，无业务通用组件进入 `frontend/src/components/`。
- `TaskHistoryPage.tsx` 只编排查询、工具栏和数据源；记录展示进入 `TaskLibraryWorkspace.tsx`，选择与键盘范围操作进入 `useTaskLibrarySelection.ts`。
- 任务创建来源解析、提交参数、总结设置和模型可用性分别复用 `features/task-creation/model.ts`、`features/summary-settings/model.ts` 和 `features/profile-selection/model.ts`，页面与弹窗不得另写同类规则。
- 原生 `<dialog>` 只由 `components/ModalDialog.tsx` 持有；表单、确认和业务弹窗通过共享容器组合，不得各自实现焦点恢复、Escape、遮罩点击和忙碌态关闭逻辑。
- `styles/features/create-task.css`、`task-library.css`、`task-detail.css` 和 `settings.css` 只作为有序 `@import` 入口；新增规则进入对应同名目录的作用域分片，并保持导入顺序。
- 架构边界由 `tests/test_architecture_boundaries.py` 约束；不得仅调高预算绕过拆分。
- API 只绑定 `127.0.0.1`。来源适配器必须保留 HTTPS、域名、DNS、重定向和资源主机校验。
- `transcript.json` 是字幕真相来源；其他字幕、对齐、说话人和导出格式均为可重建派生产物。
- 用户原始文件永不删除。任务删除只允许终态任务，并保留批量原子性、恢复和云端清理保护。

## 安全

- 密钥只进入系统凭据存储；自定义提示词使用当前用户的数据保护能力保存。
- Cookie 只用于用户有权访问的内容，按平台域过滤并驻留进程内存；不得写入数据库、日志、任务快照、测试或仓库。
- 日志和错误必须经过敏感信息清理；不得永久保存上游原始响应。
- 云端状态未知时禁止盲目重复付费提交；阶段失败必须保留已完成成果。

## 前端

- 保持“新总结、总结记录、合集管理、设置”四项信息架构；设置子页面继续由统一设置布局承载，不增加账号、会员、统计卡或说明性页面。
- 视觉、排版、层级与动效以 `design-system/vtnote/MASTER.md` 和 `frontend/src/styles/tokens.css` 为基线；使用暖中性色、墨色正文和单一低饱和铜色强调，成功、警告、危险色只表达对应状态。
- 不使用装饰性渐变、玻璃拟态、发光、悬浮抬升或弹跳动效；交互反馈保持短促、可中断，并遵守 `prefers-reduced-motion`。
- 页面文案只保留标题、字段、状态和可执行动作需要的信息；不得添加英文眉题、重复副标题、操作提示或其他无助于当前决策的说明文字。
- 图标按钮保留可访问名称；交互必须键盘可达并提供正确的 `aria-*` 状态。
- 分段导航统一复用 `components/SegmentedTabs.tsx`；弹窗、菜单、Select、Toast 和页面切换复用共享浮层与 `MotionPresence`，不得在业务页面复制焦点管理、键盘导航、碰撞定位或进出场实现。
- 内容库选择保持文件管理器语义；活动任务不进入删除选择集。
- 总结记录和合集详情继续复用 `frontend/src/features/task-library/TaskLibraryRow.tsx`；页面级列宽与响应式差异必须限制在各自页面作用域，避免改变其他视图。
- 全局提示统一复用 `frontend/src/features/task-queue/TaskQueueProvider.tsx` 中的 Toast 入口和共享组件；Toast 不显示关闭按钮，只允许自动消失或向右拖拽移除。
- 原文、字幕等阅读视图只上报时间戳；在线视频定位统一由 `SourceVideoPanel` 处理，不得在多个页面分别操作播放器。
- 修改前端后必须重新构建，确保 Python 服务读取到最新生产页面。

## 验证与交付

从项目根按影响面运行：

```powershell
conda run -n vtnote python -m pytest -q
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test:e2e:install
npm --prefix frontend run test:e2e
conda run -n vtnote python tools/package.py
git diff --check
```

- 安装验证使用 `environment.yml` 和 `frontend/package-lock.json`，不得依赖已有 editable 安装判断成功。
- 打包产物必须位于项目 `dist/`，wheel 必须包含生产前端、模型清单和内置验证音频。
- 启动验证至少检查 `/api/health` 与 `/api/readiness`；测试服务和打包暂存只使用项目内 `.vtnote/Cache/` 隔离数据。
- README 只保留用途、目录结构、运行、测试和部署/打包信息；详细产品与技术合同放在 `docs/`。
- 交付说明必须包含修改范围、验证结果和未执行的真实外部服务测试。
