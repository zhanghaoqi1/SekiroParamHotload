# 应用标记

原创几何图标：刀锋代表只狼参数工具，开放的循环箭头代表持续热加载。墨绿底、浅金环、骨白刀锋和朱红护手与 1.3 界面配色一致。没有使用游戏封面、截图或官方徽标。

- `app-icon.svg`：可编辑矢量源文件。
- `app-icon.png`：512 px 透明 PNG。
- `app.ico`：16、20、24、32、40、48、64、128、256 px Windows 图标。
- `app-icon-56.png`：界面标题区域的标记，其他小尺寸用于窗口适配。

`python assets/build_icon.py` 使用 Pillow 从同一组坐标绘制并抗锯齿缩小；Pillow 仅用于构建资产，程序运行不依赖它。SVG 的圆弧采用居中描边，栅格生成相应补偿绘制边界。

EXE 图标由 PyInstaller 的 `--icon assets/app.ico` 嵌入；窗口图标通过 Tk iconphoto/iconbitmap 加载，任务栏使用独立的 AppUserModelID。
