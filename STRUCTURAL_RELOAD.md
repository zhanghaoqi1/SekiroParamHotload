# 1.2 原生行集合热加载

2026-10-16，已实现并接入 GUI。地址适用于当前 sekiro.exe SHA-256：
637aca527538c0ec6e1f136c8ed66046e95dfbdbb1f51926e134d9916398b856

所有 RVA 相对运行时 EXE 基址 M，不是固定绝对地址。

| 对象或接口 | 布局 / RVA |
| --- | --- |
| SoloParamRepository 单例指针 | M + 0x3D978B0 |
| 参数资源链 | repo + tableOffset → holder；holder + 0x70 → cap；cap + 0x70 → PARAM |
| FD4ParamResCap 虚表 | M + 0x3280EB0 |
| 资源大小 / 数据 | cap + 0x68 / cap + 0x70 |
| 所属仓库 / 分配器 | cap + 0x48 → owner；owner + 8 → allocator |
| 分配器虚表 | M + 0x2BB9B80 |
| 对齐分配 / 释放 | 虚函数槽 10(this,size,16) / 13(this,block) |
| SprjTaskImp 单例指针 / 虚表 | M + 0x3EA1488 / M + 0x2BD8710 |
| 注册任务 | M + 0x11DF640，(this, group=0, zero, task) |
| PARAM 按 ID 查询 | M + 0x61E220，({unused=0,paramPointer},uint32 ID) → rowData 或 0 |

任务对象使用静态、固定生命周期虚表；execute 在实际 FrameBegin 线程运行。DLL 通过正常 LoadLibraryW 加载，不对游戏函数安装 detour。现有 Mod Engine 的加载器 hook 保留。

运行时块：16 字节元数据 + PARAM 文件区 + 对齐填充 + ID 查找表。元数据首 u32 是文件区大小；PARAM + 0xA 是 u16 行数；描述符在 +0x40，步长 24，ID 在 +0，行数据相对偏移在 +8。索引从 PARAM + align_up(fileSize,16) 开始，每项 (u32 ID,u32 descriptorIndex)，按 ID 排序。

结构更新重建行数、描述符、偏移、类型字符串与 ID 索引；原生查询逐 ID 验证实际返回的行指针。候选块使用资源仓库的游戏分配器创建。纯数值更新保留当前地址。

替换前将共同 ID 的数据镜像到当前及退役块，再发布新大小和指针。删除行从新索引移除，旧块中的数据保留，避免已缓存指针悬空。只有从未发布的候选块能立即释放；保留块、DLL 和任务随游戏退出。

这不等于刷新全部对象缓存。现有角色、弹丸和效果可能保存索引、复制值或派生属性。未完成所有场景切换、卸载路径的长期兼容验证。

固定源码资料仅用于核对 ABI，未复制或链接 Rust 实现：

- [PARAM 与查找表](https://github.com/vswarte/fromsoftware-rs/blob/6933967b176332a37e9ce6773f8dcd74b9feaf42/crates/sekiro/src/fd4/param_repository.rs)
- [资源仓库](https://github.com/vswarte/fromsoftware-rs/blob/6933967b176332a37e9ce6773f8dcd74b9feaf42/crates/sekiro/src/fd4/resource.rs)
- [分配器](https://github.com/vswarte/fromsoftware-rs/blob/6933967b176332a37e9ce6773f8dcd74b9feaf42/crates/sekiro/src/dlkr/allocator.rs)
- [任务接口](https://github.com/vswarte/fromsoftware-rs/blob/6933967b176332a37e9ce6773f8dcd74b9feaf42/crates/sekiro/src/sprj/task.rs)
