# 只狼参数热加载器 1.3

适用于本机 Windows x64《只狼：影逝二度》1.06（文件版本 1.6.0.0）。1.2 增加原生帧回调，支持已有参数表内真正的新增行、删除行、修改 ID 和重排。GUI 默认选择“完整行集合”。支持整份 gameparam.parambnd.dcx、单个 .param 和松散参数目录。

## 1.3 界面与图标

重新设计为暖灰卡片、深色标题与日志区、朱红主操作。路径设置和会话状态分栏，右侧显示最近一次完整行集合事务的新增、修改和删除计数。日志显示简洁时间，完整原始日志仍保存在 state/hotload.log。高级设置可展开，使用说明可滚动。

新增原创刀锋与循环箭头图标，嵌入 GUI 和后台 EXE，并用于窗口和任务栏；编辑源文件为 assets/app-icon.svg，Windows 图标为 assets/app.ico。保留 1.2.1 的热加载与长字符串区修复，原生 DLL 不变。

## 1.2.1 修复

修复 BehaviorParam、Bullet 等表字符串区超过 64 KiB 时的原生预检误报 (-33)。现在只限制类型名扫描长度，保留对行边界、名称偏移、终止符和 ID 索引的检查。原生失败日志增加具体表名与候选镜像/游戏当前镜像的区分。使用独立 release2 模块，旧版与回滚记录保留。

## 使用

1. 停止旧版工具，打开 dist/SekiroParamHotload-1.3/SekiroParamHotloadGUI.exe。保留整个目录，包含控制台后台和 _internal。
2. 默认游戏目录为 D:\SteamLibrary\steamapps\common\Sekiro。按现有 Mod Engine 配置自动识别 mods/param/gameparam/gameparam.parambnd.dcx。
3. 点击“检查参数文件”，进入存档后点击“只读检查”。只读检查不加载原生模块、不写入游戏。
4. 选择“完整行集合”，点击“启动监视”。保存后自动应用；日志报告新增、删除、修改和重排数量。
5. 停止及关闭窗口均等待当前事务结束。停止会保留已应用数据。

首次实际修改时通过 Windows LoadLibraryW 加载原生 DLL，注册 FrameBegin 任务。DLL 与回调固定到游戏退出；控制器退出后回调空闲。游戏以管理员权限运行时，工具需要同等权限。

高级设置可筛选表名、调整单次行数上限。默认 2,048 行，检查差异后可提高至 50,000。仅同步包中已有公开映射且当前已加载的表；未映射辅助表跳过。包中缺少整张表表示不更新它，不会卸载它。

```powershell
python hotload.py --full-reload
python hotload.py --full-reload --dry-run --once
python hotload.py --full-reload --source 'D:\my-mod\gameparam.parambnd.dcx'
python hotload.py --full-reload --tables AtkParam_Pc SpEffectParam --max-rows 4096
# 打包版本使用相同参数
.\SekiroParamHotload.exe --full-reload
```

启动热加载.cmd 默认完整模式。GUI 保留严格匹配和仅更新已有行两个旧模式：只做原位更新；部分同步跳过新增行并保留缺失旧行。直接运行 CLI 不指定模式时仍为严格模式；模式参数互斥。

## 实现

每 300 ms 检查文件集合、大小及修改时间，稳定 900 ms 后读取；每秒追加 SHA-256 检查，解析前后核对字节和元数据。KRAK 解压调用原游戏目录的 oo2core_6_win64.dll!OodleLZ_Decompress，再解包 BND4，严格解析各 PARAM。

完整模式流程：文件预检 → 保存全表回滚镜像 → 上传候选块 → 游戏 FrameBegin 回调 → 游戏分配器分配 → 重建描述符与排序 ID 索引 → 原生查询验证 → 切换资源 → 回读校验。

纯数值编辑保持当前资源地址，并镜像共同 ID 的修改到退役旧块。结构编辑分配新块，旧块保留到游戏退出，避免缓存旧行指针悬空。保留量达到 512 MiB 或 8,192 个版本前拒绝继续增加；重启游戏释放进程内数据。

预检固定 EXE 和 DLL 的 SHA-256，核对类型、版本、行宽、资源与分配器虚表、偏移及查找索引。各表全部准备后才发布；准备失败释放尚未发布的候选块。发布校验失败恢复指针及旧行数据，并保留曾发布块。请求超时或回读异常停止自动更新。原生准备阶段在各表之间检查 1.5 秒预算，不能视为 Windows 硬实时保证。

完整模式在游戏帧任务中执行，不暂停整个进程、不修改代码页。旧模式使用进程暂停和独立恢复看门狗。工具不修改游戏 EXE、Mod Engine 配置、参数包、存档或 dinput8.dll。详细 ABI 和地址见 STRUCTURAL_RELOAD.md，验证范围见 VALIDATION.md。

## 回滚与限制

GUI 可选择 state/journals/*.json，或先停止监视后执行：

```powershell
.\SekiroParamHotload.exe --restore '.\state\journals\实际记录.json'
```

选择数据记录而非 .result.json。记录必须属于同一进程会话、模块基址和 EXE，当前内容须匹配记录的新值；回滚自身也生成记录。格式 1/2 自动选择对应实现。磁盘源文件不变，下次监视会再次应用源文件。

- 类型、版本、字段布局或行宽变化仍拒绝；重新分配无法让游戏代码理解不同字段结构。
- 不能注册游戏原本没有的表类型。原始空表没有可验证行宽时拒绝猜测；本会话先删空的表可以利用已记录行宽加回行。
- 删除被其他参数或玩法引用的 ID 仍可能导致异常；没有完整 PARAMDEF 和跨表语义检查。
- 删除后新的 ID 查询不再返回该行，已有对象或效果不会被自动销毁。NPC、弹丸、持续效果中的派生属性可能需重新生成或应用。
- 旧块保留降低悬空指针风险，不能保证所有 Mod、场景切换及缓存行为。不能承诺任意编辑绝不崩溃。
- 重复 ID 按描述符出现次序对应；原生查询遵循游戏自身规则，不保证每个重复行都被玩法选中。

## 源码与构建

structural.py 负责完整预检和镜像日志，native.py 负责加载及共享内存协议；native/agent.cpp 与 param_engine.h 负责帧任务、分配、验证、发布和旧块保留。engine.py 保留原位模式，runtime.py 提供运行时布局检查和离线镜像构造。

```powershell
$env:SEKIRO_GUI_TEST='1'
python -m unittest discover -s tests -v
# Zig 0.16 安装于 .native-deps/ziglang
powershell -ExecutionPolicy Bypass -File native/build-agent.ps1 -Tag release2
# 重建 DLL 后须更新 profile.json 的 SHA-256；已加载 DLL 不能覆盖
powershell -ExecutionPolicy Bypass -File build.ps1
```

1.2 输出与旧版分开，保留原有 state/journals。发布包不含游戏数据或 Oodle DLL。来源与许可见 THIRD_PARTY.md。
