"""Presentation layer for the native Tk application; no game access."""
import tkinter as tk
from tkinter import ttk, font as tkfont

PAPER = '#f2f0ea'
CARD = '#fcfbf7'
INK = '#242e28'
MUTED = '#768078'
LINE = '#dddcd2'
ACCENT = '#b94930'
DARK = '#19211e'


def build_interface(app, assets, version, state_dir, app_dir):
    root = app.root
    root.title(f'只狼参数热加载器 · {version}')
    width = min(1240,root.winfo_screenwidth()-80)
    height = min(1000,root.winfo_screenheight()-90)
    root.geometry(f'{width}x{height}')
    root.minsize(min(1100,width),min(850,height))
    root.configure(bg=PAPER)
    for name in ('TkDefaultFont','TkTextFont','TkMenuFont'):
        tkfont.nametofont(name).configure(family='Microsoft YaHei UI',size=10)
    app.icon_images = []
    try:
        icon=tk.PhotoImage(file=str(assets/'app-icon.png'))
        root.iconphoto(True,icon)
        root.iconbitmap(str(assets/'app.ico'))
        app.icon_images.extend([icon,tk.PhotoImage(file=str(assets/'app-icon-56.png'))])
    except tk.TclError:
        pass

    style=ttk.Style(root)
    style.theme_use('clam')
    style.configure('.',font=('Microsoft YaHei UI',10),background=PAPER,foreground=INK)
    style.configure('TFrame',background=PAPER)
    style.configure('Card.TFrame',background=CARD)
    style.configure('TLabel',background=PAPER,foreground=INK)
    style.configure('Card.TLabel',background=CARD)
    style.configure('Muted.TLabel',foreground=MUTED)
    style.configure('CardMuted.TLabel',background=CARD,foreground=MUTED,font=('Microsoft YaHei UI',9))
    style.configure('Section.TLabel',background=CARD,font=('Microsoft YaHei UI',12,'bold'))
    style.configure('TButton',padding=(14,9),background='#e7e7de',foreground=INK,borderwidth=0,focusthickness=1,focuscolor=ACCENT)
    style.map('TButton',background=[('disabled','#ecece4'),('active','#dcded3')],foreground=[('disabled','#a3a79f')])
    style.configure('Primary.TButton',background=ACCENT,foreground='white',font=('Microsoft YaHei UI',10,'bold'),padding=(22,10))
    style.map('Primary.TButton',background=[('disabled','#d5b6ac'),('active','#9e3925')],foreground=[('disabled','#f7eee9')])
    style.configure('Quiet.TButton',background=CARD,foreground=MUTED,padding=(10,7))
    style.map('Quiet.TButton',background=[('disabled',CARD),('active','#efeee6')],foreground=[('disabled','#aaa99e')])
    style.configure('TEntry',padding=(10,9),fieldbackground='#ffffff',bordercolor=LINE,lightcolor=LINE,darkcolor=LINE)
    style.map('TEntry',bordercolor=[('focus',ACCENT)],fieldbackground=[('disabled','#f0f0e8')])
    style.configure('TSpinbox',padding=7,fieldbackground='white',bordercolor=LINE,arrowsize=12)
    style.configure('TRadiobutton',background=CARD,padding=(0,5),font=('Microsoft YaHei UI',10,'bold'),indicatorcolor='#e4e4d9')
    style.map('TRadiobutton',background=[('active',CARD)],indicatorcolor=[('selected',ACCENT),('disabled','#d5d7cb')])
    style.configure('TCheckbutton',background=PAPER,foreground=MUTED)
    style.configure('Card.TCheckbutton',background=CARD,foreground=MUTED)
    style.map('Card.TCheckbutton',background=[('active',CARD)])
    style.configure('TNotebook',background=PAPER,borderwidth=0,tabmargins=(0,0,0,0))
    style.configure('TNotebook.Tab',padding=(18,9),background='#e6e6dc',foreground=MUTED,borderwidth=0)
    style.map('TNotebook.Tab',background=[('selected',DARK),('active','#dcdfd3')],foreground=[('selected','#f4f2e8')])
    style.configure('Vertical.TScrollbar',background='#465049',troughcolor=DARK,borderwidth=0,arrowsize=12)

    def label(parent,text=None,variable=None,style='Card.TLabel',**kw):
        return ttk.Label(parent,text=text,textvariable=variable,style=style,**kw)

    def card(parent,**kw):
        return tk.Frame(parent,bg=CARD,highlightbackground=LINE,highlightthickness=1,**kw)

    def config_button(parent,text,command,**kw):
        button=ttk.Button(parent,text=text,command=command,**kw)
        app.config_widgets.append(button)
        return button

    root.columnconfigure(0,weight=1)
    root.rowconfigure(3,weight=1)
    header=tk.Frame(root,bg=DARK,padx=28,pady=20)
    header.grid(row=0,column=0,sticky='ew')
    header.columnconfigure(1,weight=1)
    if len(app.icon_images)>1:
        tk.Label(header,image=app.icon_images[1],bg=DARK).grid(row=0,column=0,rowspan=2,padx=(0,16))
    tk.Label(header,text='只狼 · 参数热加载',bg=DARK,fg='#f3f0e5',font=('Microsoft YaHei UI',22,'bold')).grid(row=0,column=1,sticky='w')
    tk.Label(header,text='SEKIRO  /  PARAM HOTLOAD',bg=DARK,fg='#b9a27b',font=('Segoe UI',9)).grid(row=1,column=1,sticky='w',pady=(4,0))
    tk.Label(header,text=f'v{version}',bg='#2b352f',fg='#d8c19b',padx=12,pady=5,font=('Segoe UI',10,'bold')).grid(row=0,column=2,sticky='e')
    tk.Label(header,text='游戏 1.06  ·  Windows x64',bg=DARK,fg='#929d93',font=('Microsoft YaHei UI',9)).grid(row=1,column=2,sticky='e',pady=(5,0))

    body=ttk.Frame(root)
    body.grid(row=1,column=0,sticky='ew',padx=24,pady=(20,0))
    body.columnconfigure(0,weight=1)
    body.columnconfigure(1,weight=0,minsize=286)
    settings=card(body)
    settings.grid(row=0,column=0,sticky='nsew',padx=(0,16))
    settings.columnconfigure(0,weight=1)
    content=ttk.Frame(settings,style='Card.TFrame',padding=18)
    content.grid(row=0,column=0,sticky='ew')
    content.columnconfigure(0,weight=1)
    heading=ttk.Frame(content,style='Card.TFrame')
    heading.grid(row=0,column=0,sticky='ew',pady=(0,12))
    heading.columnconfigure(0,weight=1)
    label(heading,'加载设置',style='Section.TLabel').grid(row=0,column=0,sticky='w')
    advanced=ttk.Checkbutton(heading,text='高级设置',variable=app.advanced,command=app.toggle_advanced,style='Card.TCheckbutton')
    advanced.grid(row=0,column=1,sticky='e')
    app.config_widgets.append(advanced)

    for row,(title,var,actions) in enumerate([
        ('游戏目录',app.game,[('选择目录',app.choose_game),('自动识别',app.detect_source)]),
        ('参数来源',app.source,[('选择文件',app.choose_source),('参数目录',app.choose_loose)])]):
        label(content,title).grid(row=1+row*2,column=0,sticky='w',pady=(0,5))
        path=ttk.Frame(content,style='Card.TFrame')
        path.grid(row=2+row*2,column=0,sticky='ew',pady=(0,12))
        path.columnconfigure(0,weight=1)
        entry=ttk.Entry(path,textvariable=var,width=20)
        entry.grid(row=0,column=0,sticky='ew')
        app.config_widgets.append(entry)
        for i,(text,command) in enumerate(actions):
            config_button(path,text,command).grid(row=0,column=i+1,padx=(8,0))

    mode_header=ttk.Frame(content,style='Card.TFrame')
    mode_header.grid(row=5,column=0,sticky='ew',pady=(1,6))
    mode_header.columnconfigure(0,weight=1)
    label(mode_header,'同步方式').grid(row=0,column=0,sticky='w')
    label(mode_header,'保存稳定后自动应用',style='CardMuted.TLabel').grid(row=0,column=1,sticky='e')
    modes=ttk.Frame(content,style='Card.TFrame')
    modes.grid(row=6,column=0,sticky='ew')
    modes.columnconfigure(0,weight=1)
    app.mode_cards=[]
    for i,(name,value,hint) in enumerate([
        ('完整行集合','full','新增、删除、修改 ID 与重排'),
        ('严格匹配','strict','仅更新数据，要求 ID 集合一致'),
        ('仅更新已有行','partial','跳过新增行，保留缺失的旧行')]):
        frame=tk.Frame(modes,bg=CARD,highlightthickness=1,highlightbackground=LINE,padx=10,pady=3)
        frame.grid(row=i,column=0,sticky='ew',pady=(0,5))
        frame.columnconfigure(1,weight=1)
        radio=ttk.Radiobutton(frame,text=name,variable=app.reload_mode,value=value,width=14)
        radio.grid(row=0,column=0,sticky='w')
        app.config_widgets.append(radio)
        label(frame,hint,style='CardMuted.TLabel').grid(row=0,column=1,sticky='w',padx=(8,0))
        app.mode_cards.append((frame,value))

    def mode_changed(*_):
        for frame,value in app.mode_cards:
            frame.configure(highlightbackground=ACCENT if app.reload_mode.get()==value else LINE)
    app.reload_mode.trace_add('write',mode_changed)
    mode_changed()

    advanced_frame=ttk.Frame(content,style='Card.TFrame')
    advanced_frame.grid(row=7,column=0,sticky='ew',pady=(9,0))
    advanced_frame.columnconfigure(1,weight=1)
    label(advanced_frame,'参数表筛选',style='CardMuted.TLabel').grid(row=0,column=0,sticky='w',padx=(0,10))
    tables_entry=ttk.Entry(advanced_frame,textvariable=app.tables)
    tables_entry.grid(row=0,column=1,sticky='ew')
    app.config_widgets.append(tables_entry)
    label(advanced_frame,'单次行数',style='CardMuted.TLabel').grid(row=1,column=0,sticky='w',pady=(7,0))
    limits=ttk.Frame(advanced_frame,style='Card.TFrame')
    limits.grid(row=1,column=1,sticky='ew',pady=(7,0))
    spin=ttk.Spinbox(limits,textvariable=app.max_rows,from_=1,to=50000,width=8)
    spin.pack(side='left')
    app.config_widgets.append(spin)
    label(limits,'表名留空加载全部；多个表名用逗号分隔',style='CardMuted.TLabel').pack(side='left',padx=12)
    app.advanced_widgets=[advanced_frame]
    app.toggle_advanced()

    rail=card(body)
    rail.grid(row=0,column=1,sticky='nsew')
    rail.columnconfigure(0,weight=1)
    session=ttk.Frame(rail,style='Card.TFrame',padding=18)
    session.grid(row=0,column=0,sticky='ew')
    session.columnconfigure(0,weight=1)
    label(session,'会话状态',style='Section.TLabel').grid(row=0,column=0,sticky='w',pady=(0,12))
    app.status_badge=tk.Label(session,textvariable=app.status,bg='#e8ebe2',fg='#5c685b',padx=12,pady=7,font=('Microsoft YaHei UI',10,'bold'),anchor='w')
    app.status_badge.grid(row=1,column=0,sticky='w')
    label(session,variable=app.detail,wraplength=242,justify='left').grid(row=2,column=0,sticky='ew',pady=(10,14))
    tk.Frame(session,bg=LINE,height=1).grid(row=3,column=0,sticky='ew',pady=(0,12))
    label(session,'参数包',style='CardMuted.TLabel').grid(row=4,column=0,sticky='w')
    label(session,variable=app.summary,wraplength=242,justify='left').grid(row=5,column=0,sticky='ew',pady=(5,13))
    label(session,'本次差异',style='CardMuted.TLabel').grid(row=6,column=0,sticky='w',pady=(0,4))
    counts=ttk.Frame(session,style='Card.TFrame')
    counts.grid(row=7,column=0,sticky='ew')
    for i,(title,var,color) in enumerate([('新增',app.added_count,'#3e7356'),('修改',app.modified_count,'#987345'),('删除',app.deleted_count,ACCENT)]):
        counts.columnconfigure(i,weight=1,uniform='counts')
        tk.Label(counts,textvariable=var,bg=CARD,fg=color,font=('Segoe UI',23,'bold')).grid(row=0,column=i,sticky='w')
        label(counts,title,style='CardMuted.TLabel').grid(row=1,column=i,sticky='w')
    label(session,variable=app.last_sync,style='CardMuted.TLabel',wraplength=242,justify='left').grid(row=8,column=0,sticky='ew',pady=(14,0))

    actions=ttk.Frame(root)
    actions.grid(row=2,column=0,sticky='ew',padx=24,pady=(14,16))
    for i,(title,mode,primary) in enumerate([('启动监视','watch',True),('只读检查','check',False),('检查参数文件','inspect',False),('选择回滚记录','restore',False)]):
        button=ttk.Button(actions,text=title,command=lambda m=mode: app.start(m),style='Primary.TButton' if primary else 'TButton')
        button.grid(row=0,column=i,padx=(0,9))
        app.buttons.append(button)
    actions.columnconfigure(4,weight=1)
    app.stop_button=ttk.Button(actions,text='停止监视',command=app.stop,state='disabled')
    app.stop_button.grid(row=0,column=5,sticky='e')

    notebook=ttk.Notebook(root)
    notebook.grid(row=3,column=0,sticky='nsew',padx=24)
    log_tab=tk.Frame(notebook,bg=DARK)
    log_tab.rowconfigure(0,weight=1)
    log_tab.columnconfigure(0,weight=1)
    app.log=tk.Text(log_tab,wrap='word',bg=DARK,fg='#d7dfd6',font=('Microsoft YaHei UI',10),borderwidth=0,highlightthickness=0,
                    padx=18,pady=14,state='disabled',height=7,insertbackground='#d7dfd6',selectbackground='#455448',spacing1=2,spacing3=3)
    app.log.grid(row=0,column=0,sticky='nsew')
    scroll=ttk.Scrollbar(log_tab,command=app.log.yview)
    scroll.grid(row=0,column=1,sticky='ns')
    app.log.configure(yscrollcommand=scroll.set)
    for tag,color in [('WARNING','#e1bb7c'),('ERROR','#f18f78'),('INFO','#d7dfd6'),('TIME','#7d9181')]:
        app.log.tag_configure(tag,foreground=color)
    notebook.add(log_tab,text='运行日志')
    help_tab=ttk.Frame(notebook,padding=18)
    help_tab.columnconfigure(0,weight=1)
    help_tab.rowconfigure(0,weight=1)
    help_text=tk.Text(help_tab,wrap='word',bg=PAPER,fg=INK,font=('Microsoft YaHei UI',10),borderwidth=0,highlightthickness=0,padx=6,pady=4,spacing3=10)
    help_text.insert('end','开始使用\n选择游戏目录与参数来源 → 检查参数文件 → 进入存档后运行只读检查 → 启动监视。\n\n'
        '完整行集合\n支持新增、删除、修改 ID 与重排。保存文件稳定 900 ms 后自动应用；只读检查不会写入游戏。\n\n'
        '严格匹配与部分同步\n严格匹配要求 ID 集合一致。仅更新已有行会跳过新行、保留文件中缺失的旧行，不能视为整包完整应用。\n\n'
        '停止与恢复\n停止和关闭窗口会等待当前事务结束，已应用的参数会保留。回滚记录仅适用于同一游戏会话且内容仍然匹配。\n\n'
        '生效边界\n部分 NPC、弹丸或效果需要重新生成或应用。版本和行大小不兼容会拒绝；删除被玩法引用的行可能导致异常。旧块保留上限为 512 MiB，达到后需要重启游戏。详细方案与限制见完整说明。')
    help_text.configure(state='disabled')
    help_text.grid(row=0,column=0,sticky='nsew')
    help_scroll=ttk.Scrollbar(help_tab,command=help_text.yview)
    help_scroll.grid(row=0,column=1,sticky='ns')
    help_text.configure(yscrollcommand=help_scroll.set)
    notebook.add(help_tab,text='使用说明')

    footer=ttk.Frame(root)
    footer.grid(row=4,column=0,sticky='ew',padx=24,pady=(10,14))
    ttk.Checkbutton(footer,text='自动滚动',variable=app.autoscroll).pack(side='left')
    ttk.Button(footer,text='清空显示',command=app.clear_log,style='Quiet.TButton').pack(side='left',padx=8)
    ttk.Button(footer,text='打开日志目录',command=lambda:app.open_local(state_dir),style='Quiet.TButton').pack(side='right')
    ttk.Button(footer,text='完整说明',command=lambda:app.open_local(app_dir/'README.md'),style='Quiet.TButton').pack(side='right',padx=8)
