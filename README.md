# Photoshop Edge Cleanup

参考图驱动的 Photoshop 去黑边与透明图导出：**Codex Skill + Windows 本地批处理工具**。

Codex 对照参考图判断哪些轮廓需要去除、哪些属于设计本身；多余外围描边通过蒙版抠成透明，保留实体牌面和图案。本地脚本限制修改范围，由 Photoshop 保存透明 PNG 和 PSD 检查点，最后逐图查看结果并核对目标边框的 alpha。

适合指示牌、钥匙扣、卡通挂牌等平面素材。批处理工具通过已登录的 Codex CLI 分析图片，用固定的本地工具修补，再调用 Photoshop 保存 PNG / PSD；它不是 UXP 插件。

## 一键批处理（第一版）

使用 Python 3.12 创建本地环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

先确认 Codex CLI 已安装并完成 `codex login`，然后双击 `start_windows.bat`。界面会尝试查找 Codex CLI；找不到时手动选择 `codex.exe`，并选择已安装的 `Photoshop.exe`。

1. 选择素材、参考和结果文件夹。参考可以为空；混合目录则勾选“素材与参考混放”，参考文件夹留空。
2. 点击“导入并开始”。自动配对参考、制定范围、预览检查、PS 执行、独立验收，最多从原图补修两轮。
3. 查看“自动初检通过 / 部分完成 / 异常”。双击图片行打开 PNG 对比图；结果同时包含透明 PNG、PSD 和判断记录。

“暂停”在当前图片结束后生效；重新打开工具后，选择原结果目录，载入批次并继续。相同输入和设置重复导入不会重做已完成图片。“重试异常”只重新处理部分完成和失败项，旧记录归档保留。更换模型、PS 路径或默认规则后，重新导入会创建新批次。

输出目录必须与输入目录分开。素材会复制到批次内，原图不覆盖。图片和参考会发送给所选 Codex 服务进行视觉判断；使用当前 Codex 登录账户的额度。不要在日志中填写密钥。

完整配置、目录结构、恢复行为和限制见 [批处理说明](docs/batch-workflow.md)。场景/实物参考图现在可以作为同一设计的语义参考使用；参考中同样存在的完整黑色设计轮廓默认保留，纯透明导出不要求生成区域多边形。抗锯齿残边只在窄外缘区域使用高阈值 `erase_edge`，避免扩大到主体内容。

当前规则为 **外围黑框抠成透明，禁止用补白/补色代替抠除**。内部白色牌面和原设计图案仍保留。升级后关闭并重新打开工具，重新导入创建新批次；旧批次留作历史对比，不能沿用旧“通过”结论。[首版历史记录](docs/validation-2026-11-11.md)中的心形牌补白结果已不符合现行标准。

若一批图片都属于“外围黑边全部去除”的固定类型，可在配置中启用 `simple_black_edge`。它跳过参考图判断，先利用与透明背景连通的白色描边定位黑边，再生成透明蒙版，由 Photoshop 执行删除和导出；需要保留设计黑框的图片不要启用该模式。

[透明去框修正验证](docs/validation-transparent-edge-2026-11-11.md)：心形样本实际 PS 导出已验证为真透明、白色牌面保留。自动批次仍检出未清完的浅色孤点，最终样本经主代理专项补修后通过模型初检；不计入全自动一次通过率。

## 能做什么

- 对照参考图，保留人物、文字、装饰与有意设计的黑框。
- 去除平面白底图与画布边界连通的背景，导出透明图。
- 按区域应用透明蒙版或路径抠除额外外框；实体内部的子图形边界可另用邻近颜色或纹理补片修复。
- 自动放大复查凹角、交汇点、内部子图形边界；无法可靠处理的区域保留并汇总，不等待实时人工圈选。
- 保存逐步 PSD/PNG 检查点、编辑范围和越界检查，支持回退。

不包含 Photoshop 安装程序、测试用客户图片、账户信息或历史运行文件。钢笔重设计轮廓、复杂毛发抠图与生成式重绘不属于已验证范围。

## 环境

- Windows 桌面与已安装、可正常启动的 Photoshop。
- 批处理工具需要 Python 3.12 或更高版本（已在 3.12 验证）；依赖见 requirements.txt。独立 skill 执行器只需要 Pillow。
- Photoshop **20.0.4 / CC 2019** 已实际验证；其他版本需要先测试 JSX 入口。
- 使用 skill 的 Codex 需要能够查看本地图片并执行本地 Python 脚本。

脚本自身不需要 OpenAI SDK 或 API 密钥；交互 skill 使用当前 Codex，批处理使用本机 Codex CLI 的已登录账户。

## 安装

在仓库根目录安装脚本依赖：

```powershell
python -m pip install -r requirements.txt
```

将 `skills/photoshop-edge-cleanup` 文件夹复制到你的 Codex skills 目录：默认是 `%USERPROFILE%\.codex\skills\photoshop-edge-cleanup`；设置了 `CODEX_HOME` 时使用其 `skills` 子目录。已有同名 skill 时先备份旧版。

执行器启动前配置两项环境变量。以下路径只是示例，替换为实际安装位置和工作区：

```powershell
$env:PS_EDGE_PHOTOSHOP_EXE = 'C:\Program Files\Adobe\Adobe Photoshop 2019\Photoshop.exe'
$env:PS_EDGE_WORKSPACE = 'C:\PhotoshopEdgeWorkspace'
python -X utf8 .\skills\photoshop-edge-cleanup\scripts\ps_tool.py --request .\examples\probe.json
```

`probe` 返回 `ok: true` 表示脚本入口可用。工作区内 `photoshop_agent/runtime` 保存会话、检查点和结果。未指定工作区时使用当前目录下的 `.ps-edge-workspace`。

环境变量必须传给执行器进程。已运行的 Codex 客户端不会自动继承另一个终端中新设置的变量；通过 Codex 执行 CLI 时，在同一条启动命令中设置变量，或在其 MCP 服务配置中设置 `env`。

## 使用

可以向 Codex 提出类似请求：

> 使用 $photoshop-edge-cleanup 处理指定文件夹。先匹配参考图，将多余外围描边抠成透明，保留实体牌面、人物和文字的设计线条。补色仅用于保留主体内部的局部修复；不要修改原图。导出透明 PNG、可回退 PSD 和图片对比，逐图报告通过、部分完成或失败。

没有参考图时，需要用户明确授权默认规则，例如“没有参考图的素材默认去除独立外围描边，但保留内部图文和深色实心主体”。不能把所有黑像素统一删除。

工具顺序为 `open_copy → set_plan → inspect/edit → snapshot/验收 → close_session`。每次只运行一条 Photoshop 命令；发生超时先检查已有响应和文档状态，避免重复执行已经生效的修改。

完整请求参数见 [工具说明](skills/photoshop-edge-cleanup/references/tools.md)，视觉检查和补修规则见 [SKILL.md](skills/photoshop-edge-cleanup/SKILL.md) 与 [局部修整说明](skills/photoshop-edge-cleanup/references/detail-repair.md)。

## 可选 MCP 入口

`skills/photoshop-edge-cleanup/scripts/mcp_server.py` 提供本地 stdio MCP 服务。启动命令为 `python -X utf8 <绝对路径>/mcp_server.py`，并传入上面的两个环境变量。CLI 与 MCP 共用执行器，不必同时配置。

服务提供打开副本、设置方案、查看局部图、区域修改、回退、快照、关闭与状态查询。不提供任意 JavaScript 执行接口。

## 验证与限制

运行不启动 Photoshop 的回归检查：

```powershell
python -m unittest discover -s tests -v
```

这些检查覆盖白底分离、范围保护、缺少方案时拒绝修改、补片范围裁限及 MCP 通信；它们不证明新素材的视觉处理成功。

历史上曾对 14 组素材做补色/去边开发验证；这些记录采用旧规则，不能用作当前“外围黑框抠透”规则的合格率。新规则需重新运行和验收，不能把旧版颜色修补通过项直接计入。

白色切边、材质阴影、人物轮廓和额外黑描边必须分别判断。范围像素检查通过、命令成功或者主体面积不变，都不能代替白底、灰底、深底及局部放大的视觉验收。
