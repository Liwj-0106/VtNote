# VtNote 发布检查表

## 版本与构建

- [ ] Windows 10/11 的全新环境能按 `environment.yml`、`requirements.lock` 和
  `frontend/package-lock.json` 完成安装。
- [ ] `python -m pip check`、后端测试、前端 lint/test/build 和 Playwright 核心旅程通过。
- [ ] `tools/collect_release_evidence.mjs` 对最终候选采集版本、配置与哈希。
- [ ] 前端 `dist`、Python 包和安装说明来自同一提交。

## 功能分层验证

- [ ] 默认离线测试不访问真实服务、不下载模型、不产生费用。
- [ ] Bilibili 单视频与合集/列表枚举、抖音、YouTube 平台适配、本地媒体、无字幕转 ASR、字幕导出和音频导出均用固定夹具覆盖。
- [ ] 只选“字幕原文”时不创建 notes 阶段；字幕完成后可直接导出。
- [ ] AI 笔记为独立可选分支，失败不会破坏字幕或触发重复转写。
- [ ] API/Worker 中断、租约过期、重试、取消和回收恢复语义通过。
- [ ] Faster-Whisper 的离线合同测试覆盖探测、转写、分块恢复、进度、取消、超时、
  显存不足和非法输出；明确选择本地 ASR 时不会静默改用云端服务。
- [ ] 获得本地 GPU 与测试媒体授权后，在支持的 NVIDIA GPU 上用固定短音频执行一次
  真实冒烟；记录 GPU、驱动、CUDA、CTranslate2、模型 revision、耗时和结果，并验证
  可选词级对齐与说话人聚类不会破坏规范字幕。未执行时不得宣称本地 GPU 已完成验证。
- [ ] 经内容与费用授权后，再运行腾讯 ASR、TokenHub、Bilibili 等外部冒烟测试；记录
  样本、时间、服务版本和结果，不在仓库保存密钥或原始私有内容。

## 数据、安全与隐私

- [ ] 只监听 `127.0.0.1`，Host、Origin、CSRF、SSRF、重定向和路径穿越测试通过。
- [ ] SQLite、日志、诊断信息、任务快照和构建产物不含明文密钥或自定义提示词。
- [ ] Credential Manager 引用、DPAPI 数据和云上传授权的失效规则已验证。
- [ ] 本地模型在加载前校验固定 revision、清单和文件哈希；推理只使用托管资产，
  缓存和模型文件不会携带应用凭据。
- [ ] `Data`/`Cache` 的备份、活动任务、24 小时回收区和 COS 终态清理已验证。
- [ ] 删除缓存不会被描述成无条件安全；孤立 partial、模型安装 trash 等已知缺口已
  解决或写入发行说明。

## 许可与供应链

- [ ] 项目自身许可证已经确定并添加 `LICENSE`。
- [ ] 最终发行物生成完整 SBOM/NOTICE；`third-party-notices.md` 与产物一致。
- [ ] 记录 FFmpeg `-version`、`-buildconf`、哈希及源码/许可履行方式。
- [ ] 模型权重、Deno、yt-dlp/EJS、CUDA 与所有原生库的来源和再分发条款已确认。
- [ ] 不包含 DownKyi/aria2/其 GPL FFmpeg，也没有复制 DownKyi 源码。

## 发布结论

- [ ] 未解决项有负责人和明确处置；涉及许可证、密钥泄漏、重复计费或数据损坏的
  问题不得降级放行。
- [ ] 没有外部账号、授权样本或计费许可时，只能声明“实现和离线验证完成”，不能
  宣称真实服务已通过生产验证。
