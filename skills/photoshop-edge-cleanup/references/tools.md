# 本地工具参数

所有坐标均为原图画布像素。`rect` 为 `[left,top,right,bottom]`，右下边界不包含在预览中；模型必须用预览返回的 rect/scale 换算绝对坐标。多边形为 `[[x,y],...]`，至少三个点。贝塞尔路径为 `[{"anchor":[x,y],"left":[x,y],"right":[x,y]},...]`，闭合；省略 left/right 时为直线角点。

## CLI 回退

使用当前 skill 所在目录的 `scripts/ps_tool.py`。在工作区 `photoshop_agent/runtime/pending.json` 写入一个请求，然后执行：

```powershell
python -X utf8 '<skill目录>\scripts\ps_tool.py'
```

等这一条命令结束并确认 `ok: true` 后再提交下一条；不能并发操作 Photoshop。结果在 stdout 和工作区 `photoshop_agent/runtime/last_result.json`。每个请求另外保留独立 job.jsx、request.json、response.json。执行器只接受下列操作，不支持任意 JavaScript 执行。

- `{"op":"probe"}`：验证 Photoshop 脚本入口。
- `{"op":"open_copy","source":"原始PNG绝对路径","remove_white_background":true}`：创建会话。透明输入不需要去白底。返回 session、document_id、revision、PSD/PNG 路径。
- `{"op":"set_plan","session":"会话ID","reference_path":"参考图绝对路径","reference_observations":["实际看图后的对应关系和判断依据"],"remove_rules":["这张图要去掉的边"],"preserve_rules":["这张图要保留的线和内容"],"remove_regions":[[[0,0],[100,0],[100,100],[0,100]]],"protect_regions":[[[20,20],[80,20],[80,80],[20,80]]],"uncertain_regions":[],"unresolved_notes":[]}`：仅在 revision 0 设置该图方案；返回红色去除/蓝色保护/橙色未解决的预览图。坐标是待编辑原图坐标，参考图只作为语义依据，不直接移植场景图坐标。保护区和未解决区覆盖去除区；允许多个多边形。剩余未解决区需要说明自动复判后仍无法确定的原因。不会等待人工确认。修改方案须从原图新开工作副本。
- `{"op":"inspect","session":"会话ID","revision":0,"rect":[0,0,300,200],"scale":3,"background":"white","sample_points":[[100,100]]}`：读取指定检查点预览与 RGBA 像素。background 支持 white/gray/dark/checker；省略 revision 为最近版本。缓存预览不反映用户在 PS 中做的外部手动修改，必要时先 snapshot。
- `{"op":"contract_mask","session":"会话ID","width":8,"feather":0.3,"keep_regions":[[[10,10],[40,10],[40,40],[10,40]]]}`：按像素内缩透明度蒙版，并保留显式指定的细节区域。可能误删细节，执行前必须检查并准备保留区域。
- `{"op":"paint_fringe","session":"会话ID","width":10,"regions":[[[10,10],[80,10],[80,80],[10,80]]],"colour":[80,35,20],"feather":0.3}`：仅在指定区域与当前外缘带交集补色，修补层剪贴到当前主体，保持主体形状。
- `{"op":"paint","session":"会话ID","regions":[...],"colour":[250,235,210],"opacity":100,"feather":0.3}`：在明确的局部选区补色，可使用 path 替代 regions。适合小块残留，不能大范围涂平主体。
- `{"op":"erase","session":"会话ID","path":[...],"feather":0.2}`：清除明确闭合路径内的区域，可用 regions 替代 path。
- `{"op":"restore","session":"会话ID","regions":[...]}`：从本会话的原始图层恢复局部内容，可能同时恢复旧黑描边，必须复核。
- `{"op":"mask","session":"会话ID","mask_path":"全画布灰度PNG"}`：用外部蒙版限制主体透明度。尺寸必须与原画布一致。
- `{"op":"patch","session":"会话ID","patch_path":"全画布RGBA补片PNG","regions":[...]}`：补片会在执行前按显式允许区域裁限透明度。用于相邻纹理延展或精细像素修复。
- `{"op":"native_defringe","session":"会话ID","width":2,"regions":[...]}`：PS 原生去边，可省略 regions 处理全图；不能把命令成功当作去掉实心描边。
- `{"op":"undo","session":"会话ID","checkpoint_revision":0}`：从已保存 PSD 恢复工作副本。使用持久检查点，不依赖可能被裁剪或分叉的历史索引。
- `{"op":"snapshot","session":"会话ID"}`：保存当前工作副本的新检查点。
- `{"op":"close_session","session":"会话ID","discard_unsaved_changes":true}`：关闭本工具匹配 ID 和 CE 标签的工作文档，保留已保存检查点；批量每张图交付后使用，避免暂存盘累积。未保存的手动改动也会被丢弃，因此只能在所需内容已保存后调用。

`width` 为 1..32 的整数，`feather` 为 0..3 像素，`colour` 为三个 0..255 数值。选区和路径必须落在原画布内。超时意味着应用状态待确认；先检查 Photoshop 或读取已有 job 响应，不能重复执行可能已经生效的修改。

除回退外，修整操作必须先有 set_plan。每个操作的实际效果都被限制在方案允许的编辑范围内；即使 contract_mask 在 PS 内计算全轮廓内缩，也会用原始的修改前像素恢复范围外内容。返回 scope_audit 记录越界像素数，必须为 0；它只证明范围限制生效，不评价参考理解和去边视觉质量。范围外出现变化时结果会被拒绝，先从上个持久检查点恢复，不盲目重复。

## MCP

补充参数：用户明确授权无参考默认规则时，set_plan 可省略 reference_path，使用 no_reference_default=true，并继续填写 observations、去除/保留规则和多边形。edge_limit=1..32 可将允许区进一步限制到真实透明轮廓带；返回 effective_scope_path 为实际允许范围预览。没有用户授权不能启用无参考默认。

open_copy 的 allow_edge_touching_artwork=true 仅用于已确认的平面白底素材。frame_barriers 例：[{"edge":"right","start":671,"end":1019}]，表示在白底连通搜索时封住右边界这一段已观察到的切边缺口；left/right 坐标沿 y，top/bottom 沿 x，start 和 end 均包含。只作用于搜索蒙版，不改变输入 RGB 或补画形状。先检查分离结果，确认封口没有把外部白底误保留在主体内。

`scripts/mcp_server.py` 是本地 stdio MCP 服务，暴露 `photoshop_open_artwork`、`photoshop_set_plan`、`photoshop_inspect`、`photoshop_edit`、`photoshop_undo`、`photoshop_snapshot`、`photoshop_close` 和 `photoshop_status`。`photoshop_edit` 使用 `operation` 字段替代 CLI 的 `op`，其余参数相同。inspect 和 set_plan 会直接返回 PNG 图像块和坐标/图例说明。

脚本依赖 Python 3.10+ 与 Pillow，不需要另装模型、OpenAI SDK 或 API 密钥。应用必须在同一 Windows 桌面会话中；脚本只修改匹配会话标签的工作文档。原文件 SHA-256 在各操作后复核，输出只能由执行器生成到工作区会话目录。
