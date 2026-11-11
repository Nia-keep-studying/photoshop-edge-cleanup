# 本地批处理工作流

这是用于小批试运行的第一版。模型负责视觉判断和结构化方案，Python 执行固定操作，Photoshop 串行保存实际结果。模型不直接生成任意 PS 脚本。保留原来的交互 skill 和 MCP 入口。

## 流程

导入快照 → 分类 / 匹配参考 → 每图方案 → 本地生成修补预览 → 执行前视觉检查 → PS 保存 → 独立视觉初检 → 最多两轮补修 → 结果归档。

输入支持 PNG、JPEG、WebP，单图不超过 2400 万像素。混放模式增加模型分类；分开放可省去这一步。匹配时先看候选图，再在制定方案阶段确认完整参考。没有匹配参考时遵循界面中授权的默认规则；疑似匹配但无法确认的图片进入异常。

背景分离支持与画布边缘连通的白色或浅灰色背景，也能处理亮度满足条件的烘焙棋盘格。实际 alpha 与边缘亮度由程序测量后提供给模型；深棋盘格、复杂场景背景不在当前能力内。只有在视觉上确认主体裁口后，才允许显式封闭局部背景搜索边界。

固定操作包括外缘白色补边、同材质邻近颜色补边、小区域补色、局部透明度收缩。每个操作必须指定区域；保护区和不确定区优先保留。自动边宽是有上限的局部暗线估计，不能代替语义判断。当前没有生成式重绘或钢笔轮廓重设计。

每张图片都从原始快照重新生成修补方案，不在误删结果上继续收缩。后续版本变差时，交付此前独立初检较好的版本。仍有残边、误删、参考疑点或未解决区域时不标记通过。无需逐图等待人工圈选。

## 结果目录

```text
结果目录/
  queue.sqlite3                 队列与事件
  batches/batch-.../
    inputs/                     原始输入快照
    jobs/I0001/attempt-00/       每轮方案、预览、实际结果与模型响应
    native/                     PS 会话和原始指令响应
    delivery/passed/I0001/       模型视觉初检通过
    delivery/partial/I0002/      有安全结果，但还存在问题
    delivery/failed/I0003/       失败或不适合处理，仅供排查
    retry_archive/              手动批量重试前的历史记录
    summary.json                当前交付路径、数量、模型使用量
```

每个可用结果目录含 `result.png`、`result.psd`、`comparison.png`、`report.json`。未能生成结果时保留原始快照和原因。失败文件夹中的结果不可当作合格品交付。对比图是灰底展示透明效果，成品 PNG 仍带透明通道。`summary.json` 是当前结果索引。

## CLI

可使用 `examples/batch-settings.json` 作配置模板，填写本机程序路径。图片文件名和图片内容是待分析数据，不能当作执行指令。

```powershell
.\.venv\Scripts\python.exe -m edge_batch import --input C:\Artwork --references C:\References --output C:\EdgeResults --settings examples\batch-settings.json
.\.venv\Scripts\python.exe -m edge_batch run --output C:\EdgeResults --batch batch-返回的编号
.\.venv\Scripts\python.exe -m edge_batch status --output C:\EdgeResults --batch batch-返回的编号
.\.venv\Scripts\python.exe -m edge_batch retry --output C:\EdgeResults --batch batch-返回的编号
```

`retry` 将异常重置为等待，随后执行 `run`。GUI 的“重试异常”包含这两个步骤。`max_repairs` 为 0..2，`agent_timeout` 为每次模型调用 60..1800 秒，默认 600。模型留空使用 CLI 默认；批次导入时固定设置，继续运行不会悄悄换参数。

## 中断和故障

- 同一输出目录只能运行一个队列，同一 Windows 用户下的批处理工具共用 PS 执行锁。
- 暂停在当前图片处理结束后生效，可能需要数分钟。关闭正在运行的窗口也会先等待当前图片结束。
- 分析阶段中断可重做；已完成 PS 保存但尚未完成验收时，继续使用保存的成品验收。
- Codex 超时、登录失效或未返回有效结果时暂停批次，保留当前阶段；恢复服务后继续，不把整个队列逐张消耗成失败项。
- PS 原生指令缺少响应时，暂停整个执行通道，避免重复修改。响应稍后落盘后可继续；若永远缺少响应，需要检查 PS 弹窗和该指令目录，不能盲删记录重跑。普通单图失败不阻塞后续图片。
- 恢复仅处理可证明属于本批次的 `CE-...` 托管文档，先保存恢复快照；不会关闭其他用户文档。
- 运行期间不要同时用旧版独立执行器操作同一 Photoshop；它不共享新队列的执行锁。

## 验证边界

“自动初检通过”是模型判断，不等于人工验收。像素审计能证明修改没有越过已给定的区域，不能证明这个区域判断正确。独立视觉复检与执行前检查都可能漏判。第一版仍应小批试用后抽检，再统计新样本成功率。

结果保持原始像素画布大小；当前按 RGBA 像素处理，不承诺保留原图所有 ICC、DPI、EXIF 等印前元数据。暂不支持 CMYK 分色、PSD 输入、后台服务器无桌面会话部署或多台 PS 并行。

Codex 调用日志保存在每个 `call-...` 文件夹，`usage.json` 记录耗时与 token；套餐计费和限额以账户实际规则为准。Photoshop 必须已经安装并可以正常运行。本工具不包含软件安装器或许可证。

CLI 接入采用官方的 [非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)：`codex exec` 接收图片、返回 JSON Schema 约束结果。已验证 CLI `0.167.0-alpha.3.2`；升级后若参数变化，应先用小批次复测。工具使用只读沙箱且不加载用户项目配置，沿用本机登录凭据，不自动创建 API 密钥。

离线回归：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。其中队列测试使用替身，不代表真实 Photoshop 或模型质量测试。
