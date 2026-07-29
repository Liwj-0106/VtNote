# VtNote V1 发布检查表

## 构建与依赖

- [ ] `conda env create -f environment.yml` 可在全新 D 盘测试目录完成。
- [ ] `python -m pip check` 通过，生产依赖均有精确版本。
- [ ] `npm ci`、`npm run lint`、`npm test -- --run` 和 `npm run build` 通过。
- [ ] `node tools/collect_release_evidence.mjs --output <D盘目录>` 成功。
- [ ] Deno、EJS、yt-dlp、模型 manifest、前端 lock 和 FFmpeg 哈希已记录。

## FFmpeg 与许可证

- [ ] 记录 `ffmpeg -version` 与 `ffmpeg -buildconf`。
- [ ] 发布包使用经过核验的 LGPL 候选；如果仍含 `--enable-gpl`，不得标记为 LGPL。
- [ ] `docs/third-party-notices.md` 与实际发布物一致。
- [ ] 模型权重、CUDA 运行时和所有二进制的再分发条款已人工确认。

当前开发环境的 Conda FFmpeg 7.1.1 含 `--enable-gpl`、x264 和 x265，只能作为
开发验证证据，不能冒充 LGPL 发布产物。

## 安全与隐私

- [ ] 仅监听 `127.0.0.1:8765`，生产 docs/CORS 关闭。
- [ ] Host、Origin、CSRF、SSRF、重定向、DNS 重绑定和路径穿越测试通过。
- [ ] 数据库、日志、诊断包、构建产物和任务快照不含明文密钥或自定义提示词。
- [ ] 腾讯上传授权和百炼文本授权在配置变更后失效。
- [ ] 云端未知提交不会自动重复计费；重试需要明确费用确认。

## 功能与恢复

- [ ] Bilibili/YouTube 有字幕路径、本地媒体和四种字幕格式通过离线夹具。
- [ ] 腾讯云成功、已知失败回退、未知结果、COS 清理和取消后查询通过。
- [ ] 本地 CUDA ASR、翻译、笔记和独立失败通过。
- [ ] API/worker 在每个阶段中断后可恢复，不覆盖 `transcript.json`。
- [ ] 成功后没有长期媒体副本，回收区 24 小时后有审计地清理。

## 发布边界

- [ ] 30–50 条已授权真实样本 POC 已完成并记录失败样本。
- [ ] 腾讯、Bilibili、YouTube、FFmpeg 和模型许可的最终状态已冻结。
- [ ] 若没有账号、语料或计费授权，只能标记“实现完成；在线发布资格待验证”。
