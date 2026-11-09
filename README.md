# B 站博主开播提醒

定时轮询 B 站**公开直播状态接口**，发现关注的博主从「未开播」变为「直播中」时，
按**每个博主单独配置的渠道**通知你；桌面弹窗里可以**直接点「听音频」/「看画面」接入直播流**。

- **核心监控**（`watcher.py`）零第三方依赖，仅 Python 标准库，可单独命令行运行。
- **托盘 + 设置 GUI**（`tray_app.py` / `settings_gui.py`）用 `uv` 管理，依赖 `pystray` + `Pillow`，
  由 `启动.bat` 自动装进 `.venv`，双击即用。

你给的 `https://space.bilibili.com/48330353` → UID `48330353`（双尾彗星），已作为默认配置。

## 托盘 / 设置 GUI / 启动脚本（推荐用法）

**双击 `启动.bat`** —— 它会：检查 `uv` → 没有 `.venv` 就用 `uv venv --python 3.14` 创建 →
`uv pip install pystray pillow` → 缺 `app.ico` 就调 `tools\build_icon.py` 生成 →
`start "" .venv\Scripts\pythonw.exe tray_app.py` **静默启动**（无黑框，托盘出现粉底白字「Bo」图标）。

- 首次运行需要联网装依赖；之后秒开。缓存放在项目内 `.uv-cache/`，不污染全局。
- 调试用 `调试.bat`：带控制台跑 `tray_app.py --no-tray`（不显示托盘，只跑轮询，Ctrl+C 退出）。
- 前提：本机装了 [uv](https://docs.astral.sh/uv/) 且在 PATH（本机已装 `uv 0.11.28`）。
- 两个 bat 均为**纯 ASCII + GBK + CRLF**，无中文乱码风险；中文说明都在本文档。
- 重新生成 bat：在项目根目录运行 `python tools\gen_bat.py`（`tools/gen_bat.py` 是唯一来源）。

托盘右键菜单：设置…（默认双击动作）/ **🔍 探测直播状态…** / 立即检查 / 暂停·恢复 /
听直播…（挑第一个在播的房间打开播放弹窗）/ 打开 config.json / 打开日志 / 退出。
菜单首行实时显示「N 人直播中 · HH:MM:SS 已检查」。

**🔍 探测直播状态**（启动后手动触发，与「🔄 立即检查」语义不同）：
立刻查一次所有博主并弹**汇总窗口**，表格列出 UID / 昵称 / 状态 / 房间号 / 标题 / 开播时间 / 提醒渠道；
在线行可在下方下拉选择后点「▶ 听音频」「▶ 看画面」直接播放，离线行灰显、无效 UID 标为「无返回」。
多个博主同时在线时一屏展示，不会刷屏。只查状态接口，不触发通知、不改 `state.json`。
播放复用 `watcher.play_room()`，**播放器独立窗口，关汇总窗不影响播放**。

**设置 GUI**（tkinter 单窗口）：
- 博主表格：增删行、UID、备注名、5 个渠道复选框（桌面/声音/邮件/原生 Toast/打开网页）。
- 「全局」「邮件」「播放器」三个标签页：轮询间隔、去抖、重试、超时、开关；SMTP 全套；
  播放器优先顺序与三个显式路径。
- 点「保存并应用」→ 原子写回 `config.json` → **托盘热重载**（保留已知状态，不重复提醒），
  无需重启。内置校验：UID 正整数/不重复、间隔 ≥30、启用邮件必填项。

**图标**：`app.ico` 为 9 尺寸（16/20/24/32/40/48/64/128/256）手调多帧 ICO，
粉底 `#FB7299` 圆角矩形 + 白色粗体「Bo」；`tray.png` 是托盘用的 64×64 帧。
在项目根目录运行 `.venv\Scripts\python.exe tools\build_icon.py` 重新生成、
`.venv\Scripts\python.exe tools\verify_icon.py` 做结构校验（均需 `.venv` 里的 Pillow）。

> 托盘为**主程序**：后台线程跑的就是 `watcher.py` 的 `poll_once()`，与命令行版共用同一套
> 状态机/去重/通知/播放逻辑。`watcher.py --once` / `--test-notify` / `--play-room` 仍可单独使用。

## 两个核心功能

### 1. 点击弹窗直接播放直播音频 / 视频

桌面弹窗里有 **▶ 听音频 / ▶ 看画面 / 打开网页** 三个按钮：

- 点击时**实时**向 B 站 `Room/playUrl` 接口取直播流直链（失败自动回退 `getRoomPlayInfo`），
  再交给本地播放器解码。直链带 `expires` 短时效，**不能缓存**，所以每次点击都现取。
- 自动探测播放器：`VLC → mpv → ffplay`（PATH + 常见安装路径 + 可配置显式路径）。
  本机已实测探测到 `C:\Program Files\VideoLAN\VLC\vlc.exe`。
- **音频模式**用 `--no-video`（VLC）／`--no-video`（mpv）／`-nodisp`（ffplay）只禁画面；
  视频模式则正常播放画面。
- **播放与弹窗解离**（用户要求）：VLC 保留**自己的 GUI 窗口**（已移除早期的 `--intf dummy`
  无界面模式），暂停 / 继续 / 音量 / 关闭都在 VLC 窗口里操作。通知弹窗只负责"启动播放"，
  不持有播放 token、不复制停止控制、**关掉弹窗音频继续**，退出托盘也不强杀 VLC。
  `PlaybackController` 只剩两个职责：登记当前进程、开新播放时顶替旧播放（避免多路音频叠加）。
- **防盗链关键**：实测 B 站流 CDN 无 `Referer` 直接返回 **403**，带
  `Referer: https://live.bilibili.com/` 才返回 200。所以所有播放器命令都强制注入
  Referer + User-Agent（VLC 用 `--http-referrer` 与 per-input `:http-referrer=`，见
  [VideoLAN 文档](https://wiki.videolan.org/index.php?title=Documentation:Modules/http&printable=yes)）。
- 找不到播放器 / 取流失败 → 自动降级为打开直播间网页，并写日志。
- 所有 UI（开播弹窗 / 托盘「听直播」/ 探测汇总窗口）统一走 `watcher.play_room()`。
- 也可以不改配置，手动播任意房间：`python watcher.py --play-room 1496449`。

### 2. 每个博主的提醒方式可独立配置

`config.json` 用 `streamers` 数组，每项写 `uid` + 可选的 `channels`：

```jsonc
"streamers": [
  { "uid": 48330353, "name": "双尾彗星", "channels": ["desktop", "sound", "email"] },
  { "uid": 2,        "name": "碧诗",     "channels": ["email"] }
]
```

可用的 `channels`：

| 值 | 效果 |
|---|---|
| `desktop` | tkinter 桌面弹窗（**播放按钮在这里**） |
| `sound` | Windows 提示音 |
| `email` | 邮件（需 `email.enabled=true`） |
| `native_toast` | 原生 Win10/11 Toast（可选，见下） |
| `open_browser` | 开播时自动打开直播间网页 |
| `all` | 以上全部 |
| `none` / `[]` | 静默，完全不通知 |
| 不写 `channels` | 使用**全局默认渠道**（由顶层 `desktop/sound/...` 开关推导） |

> ⚠️ 「只发邮件」的博主不会有桌面弹窗，也就没有播放按钮 —— 这是渠道语义的必然结果。
> 需要随时听某个房间，用托盘「🔍 探测直播状态…」汇总窗口（可选任意在线博主）或
> `--play-room <房间号>` 手动打开播放弹窗即可。

**兼容旧配置**：没有 `streamers` 时，仍读 `uids` 列表 + 顶层全局开关，行为与旧版一致。

## 为什么能做成 / 原理

```
状态：  GET https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids?uids[]=xxx
流地址：GET https://api.live.bilibili.com/room/v1/Room/playUrl?cid=<room>&platform=web&otype=json
备用：  GET https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo?room_id=<room>&...
```

- 全部免登录、免 Cookie。状态接口支持一次查多个 UID，返回各自
  `live_status` / `uname` / `title` / `room_id` / `live_time`。
- `live_status`：`0` 未开播、`1` 直播中、`2` 轮播中。
- 判定逻辑：**只在 `0/2 → 1` 跳变时提醒一次**；用 `live_time`（本场开播时间戳）去重，
  脚本重启、网络抖动、轮播切换都不会重复轰炸。状态持久化在 `state.json`，日志在 `watcher.log`。

参考：[RSSHub 的同类实现](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/bilibili/live-room.ts)、
[bilibili-API-collect 直播文档](https://cnb.cool/coser/bilibili-API-collect/-/blob/5d188bf9b209ca112581ee7f36ccd359f4a7953f/docs/live/info.md)。

> 备选：只想用手机收通知 → B 站 App「关注 → 开播提醒」是官方方案，或 RSSHub 接 RSS 阅读器。
> 本工具主打**电脑端、低延迟、每个博主不同提醒方式、能直接听音频**。

## 文件

按 win-env-kit 规则整理：**运行入口与运行期文件在根目录，工具/测试全部收进 `tools/`**；
一次性产物只进 `_scratch/`（已 gitignore），运行期产物已排除。

| 文件 | 作用 |
|---|---|
| `启动.bat` / `调试.bat` | 双击启动（uv 自动建 venv 装依赖 + pythonw 静默） / 带控制台调试 |
| `tray_app.py` | 托盘主程序（pystray + 后台轮询线程 + 热重载 + 探测菜单） |
| `settings_gui.py` | tkinter 设置窗口 + 直播状态探测汇总窗口 |
| `watcher.py` | 核心监控（轮询、状态机、渠道分发、取流、播放器、开播弹窗），零第三方依赖 |
| `config.json` | 配置（GUI 保存会写回这里） |
| `app.ico` / `tray.png` | 应用图标（多尺寸）/ 托盘用 64×64 PNG |
| `tools/build_icon.py` / `tools/verify_icon.py` | 生成 9 尺寸 `app.ico` / 结构校验 |
| `tools/gen_bat.py` | 生成两个 bat 的唯一来源（保证 GBK+CRLF） |
| `tools/test_watcher.py` / `tools/test_gui_logic.py` | 离线自测（核心 75 项 / 配置层 35 项） |
| `.gitignore` / `.gitattributes` | 排除运行期产物；源码 LF、Windows 脚本 CRLF |
| `state.json` / `watcher.log` | 自动生成（已被 gitignore），状态去重 / 运行日志 |
| `.venv/` / `.uv-cache/` / `_scratch/` | 自动生成（已被 gitignore），venv / uv 缓存 / 一次性产物 |

## 快速开始

```powershell
cd D:\Share\Scripts\Explore\bilibili

# 1) 只查一次：确认能连通、UID 与各博主渠道都对（不通知）
python watcher.py --once

# 2) 测试通知通道（会弹窗 + 响铃；邮件需先配好 enabled=true）
python watcher.py --test-notify

# 3) 手动打开播放弹窗，验证「听音频 / 看画面」（播放器独立窗口，弹窗可随手关）
python watcher.py --play-room 1496449

# 4) 正式常驻运行（关掉窗口即停止）
python watcher.py

# 5) 工具与测试（都已移进 tools/，在项目根目录运行）
python tools\test_watcher.py
python tools\test_gui_logic.py
.venv\Scripts\python.exe tools\verify_icon.py
```

`--once` 输出示例（注意 `channels` 按博主不同）：

```
[..] UID 48330353 | 双尾彗星 | live_status=1 | room=1496449 | channels=desktop,email,sound
[..] UID 2 | 碧诗 | live_status=0 | room=1024 | channels=email
```

## 配置说明（config.json）

```jsonc
{
  "interval": 180,              // 轮询间隔（秒）。低频建议 120~300，别低于 60
  "desktop": true,              // 全局开关 → 默认渠道
  "sound": true,
  "native_toast": false,
  "open_browser": false,
  "notify_on_start": true,      // 启动脚本时若已在直播，是否提醒一次
  "notify_on_end": false,       // 下播是否也通知（默认关）
  "debounce_polls": 1,          // 连续 N 次查到开播才确认；低频用 1，若误报改 2
  "http_timeout": 10,
  "retries": 2,

  "streamers": [                // 每个博主单独配置（见上）
    { "uid": 48330353, "name": "双尾彗星", "channels": ["desktop", "sound", "email"] },
    { "uid": 2, "name": "碧诗", "channels": ["email"] }
  ],

  "player": {                   // 可选：覆盖播放器路径 / 顺序
    "vlc_path": null,           // 例："D:\\Tools\\vlc.exe"
    "mpv_path": null,
    "ffplay_path": null,
    "prefer": ["vlc", "mpv", "ffplay"]
  },

  "email": { ... }             // 见下
}
```

### 加博主

从空间页 URL 取数字：`space.bilibili.com/48330353` → `48330353`，往 `streamers` 里加一项即可。
一次请求批量查询；无效 UID 会被跳过并在日志里提示，不影响其它人。

### 邮件通知

把 `email.enabled` 改成 `true` 并填好 SMTP。常见配置：

| 邮箱 | smtp_host | smtp_port | 说明 |
|---|---|---|---|
| QQ 邮箱 | `smtp.qq.com` | `465` | `password` 填**授权码**，不是 QQ 密码（设置 → 账户 → 开启 IMAP/SMTP 获取） |
| 163 | `smtp.163.com` | `465` | 填客户端授权密码 |
| Gmail | `smtp.gmail.com` | `465` | 填应用专用密码 |
| Outlook | `smtp.office365.com` | `587` | 需额外 `"use_ssl": false, "use_starttls": true` |

`to` 可以是字符串或字符串数组。邮件失败只记日志，不影响其它渠道。

### 原生 Toast（可选）

`desktop` 弹窗已够用且零依赖。想要系统通知中心样式的 Toast 就把 `native_toast` 设为 `true`：
脚本会调用 PowerShell 5.1 的 WinRT，并尝试在 `HKCU\Software\Classes\AppUserModelId` 注册 AUMID
（普通进程无包身份，必须注册）。某些环境（组策略 / 沙箱）会拒绝写注册表，此时该通道静默降级，
日志里能看到原因，其它渠道照常。

> 注：当前沙箱环境已实测「注册被拒」，所以默认关闭。你在本机双击运行时通常有权限。

## 开机自启（可选）

**方式 A：启动文件夹 + VBS（无黑框）**

1. `Win + R` 输入 `shell:startup` 回车；
2. 在该文件夹新建 `BiliLiveWatcher.vbs`：

```vbscript
Set ws = CreateObject("Wscript.Shell")
' 用绝对路径，不依赖 %USERPROFILE%（避免中文/空格路径被截断）
ws.CurrentDirectory = "D:\Share\Scripts\Explore\bilibili"
ws.Run "pythonw.exe D:\Share\Scripts\Explore\bilibili\watcher.py", 0, False
```

把路径换成你自己的目录。`pythonw.exe` 运行完全无控制台窗口（日志仍写入 `watcher.log`）。

**方式 B：任务计划程序**：创建基本任务 → 触发器「计算机启动时」→ 操作「启动程序」：
程序 `pythonw.exe`，参数 `watcher.py`，起始位置填本目录绝对路径。

## 防风控 / 可靠性

- 默认 180 秒一次、带浏览器 UA 与 Referer，对单个 UID 请求量极低（约 480 次/天），远低于风控阈值。
- **不要**把 `interval` 设到 60 秒以下；博主多、频率高时可加到 300。
- 网络异常会指数退避重试（最长 15 分钟），不会崩；接口短暂异常不会误判下播（无效 UID 保留旧状态）。
- `debounce_polls: 2` 可过滤接口瞬时抖动，代价是开播提醒最多晚一个轮询周期。
- 取流只在**用户点击时**发生，不随轮询请求，额外开销几乎为零。

## 已知问题：托盘不显示 / 启动时 WinError 5（同一个根因）

**两个症状，都是 2026-11-08 用户实测**：

1. 双击 `启动.bat` 后 `tray_app.py` 在
   `pystray/_win32.py:_create_window → ChangeWindowMessageFilterEx` 抛 `WinError 5` 退出；
2. 加了兼容补丁后**不再崩溃**、日志有「托盘启动」，但**通知区始终看不到图标**，
   进程随后静默消失（`pythonw.exe` 无控制台，traceback 被吞）。

**根因**（已用最小复现 + 微软文档 + `icacls` 实测确认）：

1. DSH 的 `workspace-write` 沙箱给 workspace 根目录打上 trust mark，
   `icacls` 显示 `Mandatory Label\Low Mandatory Level:(OI)(CI)(NW)`、
   `S-1-4-*`、`Everyone:(CI)(DENY)(DC)`；在该目录里创建的
   `.venv\Scripts\pythonw.exe` **继承了 Low 完整性级别**
   （`whoami /groups` 显示 `S-1-16-4096 Low Mandatory Level`）。
2. [微软文档明确规定](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-changewindowmessagefilterex)：
   进程 IL ≤ `SECURITY_MANDATORY_LOW_RID` 时 `ChangeWindowMessageFilterEx` 返回
   `ERROR_ACCESS_DENIED (5)`。pystray 用 `errcheck=_err` 把它转成异常 → 崩溃。
3. **更要命的是图标本身**：最小探针实测裸调
   `Shell_NotifyIconW(NIM_ADD)` 返回 `ok=0`（GetLastError=0，UIPI 静默拒绝），
   且 `pystray icon.visible == False`。Explorer 通知区是 Medium IL，
   **低 IL 进程根本无法向它添加图标** —— 这不是代码 bug，是 OS 安全边界。

**已做的缓解**：[tray_app.py](tray_app.py) 的 `install_low_il_compat()` 只解决症状 1
（仅吞 WinError 5，其它错误原样抛出；幂等、可注入，回归测试 `tools\test_tray_compat.py` 7 项）。
**它不能、也无法解决症状 2**：补丁不改变进程 IL。

### ✅ 根治：把项目搬出 DSH workspace（必须你来做）

`win-env-kit` 规则 9：把项目复制到**未被 trust mark 标记的干净目录**，
并由**你自己的桌面会话（Medium IL）**重建 `.venv` —— 关键是不能让
`pythonw.exe` 这个 PE 文件带 Low 标签，而 DSH 沙箱里启动的任何进程本身就是 Low IL token。

已经帮你把源码复制到了 **`D:\Apps\BiliLiveWatcher\`**，`icacls` 验证
**无 `Low Mandatory Level` / `S-1-4-*` / `Everyone DENY`**（只有正常的
Administrators / System / Users 继承 ACE）。该目录只含源码，不含 `.venv`。

**请在普通的 PowerShell / 资源管理器里执行**（不要在 DSH 会话里）：

```powershell
cd /d D:\Apps\BiliLiveWatcher
if exist .venv rmdir /s /q .venv
启动.bat
```

`启动.bat` 会重新建 venv、装依赖、生成图标并用 `pythonw.exe` 启动。
此时进程是 Medium IL，`Shell_NotifyIconW` 成功，**托盘图标就会出现**。

可复用迁移脚本：`tools\migrate.ps1`（UTF-8 BOM + CRLF，含 `-Destination` / `-DryRun`，
会自检源/目标目录的 trust mark 并排除 venv/cache/运行期产物）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\migrate.ps1 -Destination D:\Tools\Bili
```

> 换机器、换目录、或以后再遇到「图标不显示」，先跑
> `icacls <目录>` 看有没有 `Low Mandatory Level`。有 → 搬走重建 venv。

## 验证状态

✅ **已在沙箱自验**
- Python 3.14.6 标准库直连 B 站接口成功（免 Cookie，拿到昵称/状态/房间号）。
- 实测流直链防盗链：无 Referer → 403；带 Referer → 200 `video/x-flv`（magic `FLV`）。
- 实测 `fetch_stream_url` 真实取到带 `expires` 的 HTTPS 直链；`find_player` 探测到本机 VLC。
- `python tools\test_watcher.py`：**75 项离线断言全 PASS** —— 状态机、`live_time` 去重、轮播、
  去抖、多博主、无效 UID、`streamers/channels` 路由、旧配置兼容、流地址解析与回退、
  播放器探测/命令构造（Referer/UA/纯音频）、token 精确停止、昵称兜底、
  **播放解离（VLC 无 `--intf dummy`、保留 GUI 窗口）**、`play_room()` 统一入口、
  `probe_rows_to_table()` 多博主/无效 UID 表格转换。
- `python tools\test_gui_logic.py`：**35 项全 PASS** —— 配置 split/merge 往返、渠道有序化、
  校验规则、原子保存、GUI 保存结果能被 `watcher.load_config` 正确消费。
- `.venv\Scripts\python.exe tools\test_tray_compat.py`：**7 项全 PASS** —— Low IL 兼容补丁
  吞掉 WinError 5、不吞其它错误、幂等、可在真实 pystray 模块上安装。
- **WinError 5 复现与修复验证**：最小复现脚本稳定复现 `[WinError 5]`（与用户 traceback 一致）；
  打补丁后 `icon.run()` 到达 setup、窗口创建成功；原始 `tray_app.py` 全场景重跑 `app.run()`
  干净返回、零异常逃逸。
- `.venv\Scripts\python.exe tools\verify_icon.py`：`app.ico` **9 帧全通过** —— ICONDIR 结构、
  每帧 PNG 签名、四角透明、粉色采样点、白色文字占比、边界无裁切；并输出 64px ASCII 轮廓。
- `uv venv --python 3.14` + `uv pip install pystray pillow` 实测成功（pystray 0.19.5 / Pillow 13.0.0），
  venv 内含 `pythonw.exe`。
- 托盘真实冒烟：`pythonw.exe tray_app.py` 启动后持续运行 3 秒未退出、写出「托盘启动」日志；
  `poll_loop` 两轮轮询、`check_now()`/暂停/恢复正常；`pystray.Icon` + **12 项菜单**（含新增
  「🔍 探测直播状态…」）构造成功，`HAS_MENU/HAS_NOTIFICATION=True`；设置窗口真实构建通过。
- **探测汇总窗口冒烟**：真实接口查询 → `probe_rows_to_table` 转换 → tkinter 窗口 headless 构建成功
  （2 行、离线行灰显、无在线博主时播放区正确显示提示）。冒烟时 48330353 恰为「轮播中(2)」，
  正好验证状态 2 不算在线、不出现播放按钮。
- 两个 bat：纯 ASCII + CRLF + 无 BOM，重生成后内部 `tools\build_icon.py` 路径正确。
- **`node dsh-env-lint.mjs .`**：根目录源码全部 UTF-8/LF、两个 bat CRLF、无 `SCRATCH` 告警
  （venv/缓存/运行期产物已被 `.gitignore` + `--exclude` 排除）。
- `python -m py_compile`：全部模块语法检查通过；`watcher.py --once` 真实接口端到端成功。

> ⚠️ 环境限制：当前沙箱**无显示器、无视觉模型、无 VLC 音频设备**，所以图标的**实际观感**、
> 托盘图标在通知区的显示、VLC 窗口里的暂停/继续、点击播放后的声音、设置窗口的像素级布局、
> 邮件真实投递都只能靠程序化结构验证 + 本机目视/听感确认，无法在沙箱里"看到/听到"。

⏳ **需你在本机验证（GUI / 声音 / 系统权限，沙箱无法模拟）**
0. **先搬到 `D:\Apps\BiliLiveWatcher` 再启动**（见上节「根治」），否则 Low IL 下图标不会显示。
   若仍崩或仍无图标，把 `watcher.log` / 完整 traceback 发我。
1. **双击 `启动.bat`** → 确认**不再报 WinError 5**、通知区出现粉底白字「Bo」托盘图标
   （首次会联网装依赖，稍等）。
   - 顺带验证：重启一次 `explorer.exe` 后图标是否还在（搬出后应能自动重建）。
2. 右键 →「🔍 探测直播状态…」→ 汇总窗口列出所有博主；有博主在线时选一个点「▶ 听音频」，
   确认 **VLC 自己的窗口弹出**、能暂停/继续、**关掉汇总窗口或通知弹窗后音频仍在播**。
3. 右键 →「设置…」→ 改一项 →「保存并应用」→ 确认热重载生效、无需重启。
4. `app.ico` 在任务栏 / Alt-Tab / Explorer 大图标视图下逐尺寸目视：16/20/24 的「Bo」是否清晰、无白边。
5. `python watcher.py --play-room 1496449` → 点「听音频」能出声（VLC 带 Referer 不被 403）、
   点「看画面」有画面；停止播放请用 **VLC 窗口自己的关闭/停止按钮**。
6. `python watcher.py --test-notify` → 弹窗与提示音；填好 SMTP 后确认邮件收到。
7. 若要开机自启，把 `启动.bat` 的快捷方式放进 `shell:startup`。

## 架构笔记：这类「Python 小工具 + 托盘 + GUI + bat 启动」怎么组织

这次踩的坑（Low IL 图标不显示、pythonw 吞 traceback、多个 tkinter mainloop 在不同线程、
850 行单文件同时干 API/状态机/通知/播放/UI）不是偶然，是这类项目的结构性矛盾。
若以后继续演进，按下面的顺序改收益最大：

### 1. 分发形态决定一切

| 形态 | 适用 | 代价 |
|---|---|---|
| 源码 + uv + bat（当前） | 自己用、常改 | 依赖 uv/PATH；**venv 在 DSH workspace 会继承 Low IL**（本次事故）；pythonw 吞异常 |
| **`uv` + `pyproject.toml` + `uv run`**（推荐给源码分发） | 自己用 | 一个 `pyproject.toml` 锁依赖与入口点，`uv run bili-tray` 取代手写 bat 建 venv；venv 交给 uv 放在干净位置 |
| PyInstaller 单 exe | 发给别人 / 免 Python | 体积大、启动慢、pystray/Pillow 需 hidden import、每次改要重打包 |
| .NET (WinForms/WPF) 单文件 publish | 要正经托盘+GUI+安装器 | 托盘/系统通知/单实例/安装器全框架自带，无 IL 继承、无打包器、无多 GUI 线程问题 |

**结论**：只自己用 → 源码 + `uv run`；要分发 → PyInstaller，但托盘层优先原生 Win32。

### 2. 线程模型：现在是「三方抢主线程」，很脆

当前主线程 `pystray.icon.run()`、轮询 daemon 线程、每次设置窗口一个新 tkinter 线程、
通知弹窗又一个 tkinter 线程。**多个 tkinter mainloop 跑在不同线程是未定义行为**，
迟早 `RuntimeError: main thread is not in main loop`。

推荐二选一：
- **A（推荐）**：单个 tkinter 主线程 owning 所有 UI，托盘用
  `pystray.Icon.run_detached()`（官方为整合 GUI 框架提供的入口），
  轮询/通知通过 `root.after()` 或队列把事件送回主线程 —— 全进程只有一个事件循环。
- **B**：托盘后端换成原生 Win32（ctypes 直接 `Shell_NotifyIconW`），
  去掉 pystray 的隐藏窗口与消息循环，tkinter 独占主线程；顺带避开 Low IL / 提权 / 多显示器下的边界问题。

### 3. 分层：把「机制」和「界面」分开

```
core/      纯逻辑，零 GUI 依赖，可单测
  config.py    配置 schema + 校验 + 热重载
  monitor.py   状态机 / 去重 / B 站 API
  notifier.py  渠道抽象（desktop/sound/email…）→ 接口 + 实现
  player.py    流地址 + 播放器进程
ui/        tkinter 窗口（设置 / 弹窗 / 探测汇总），只调 core
tray/      托盘适配层（pystray 或 Win32），只发命令给 core
app.py     组装：单事件循环 + 依赖注入
```

现在 `watcher.py` 一个文件同时干 API、状态机、通知、播放、tkinter 弹窗 ——
这正是为什么改「播放解离」时要全局 grep 文案、加测试要绕开 GUI 依赖。
`core` 纯下来后，90% 的改动能用 `pytest` 秒验，不必靠人眼看托盘。

### 4. Windows 工程纪律（本次全踩）

- **可观测性**：`pythonw.exe` 无控制台，traceback 全吞。顶层必须 `try/except` 写 `startup.log`，
  开发期一律用 `python.exe`。
- **IL / trust mark**：agent 生成的 exe/bat/venv 都带 `Low Mandatory Level`。
  **交付前拷到干净目录**并用 `icacls` 验（本项目已提供 `tools\migrate.ps1`）。
- **单实例**：托盘工具必须互斥量（`CreateMutexW`），否则双击两次出两个图标。
- **开机自启**：用 `shell:startup` 快捷方式或任务计划，别写注册表 Run 键。
- **编码**：bat 纯 ASCII + CRLF、`.ps1` 含中文用 UTF-8 BOM + CRLF、源码/配置 UTF-8 无 BOM + LF
  （本项目 `.gitattributes` / `tools\gen_bat.py` / `tools\migrate.ps1` 已落实）。

## 打包成免安装 exe（可选）

不想装 Python / uv 的话，可以打包成免安装 exe（--onedir + 无控制台窗口，双击即托盘常驻）：

```bat
:: 首次打包前装 PyInstaller
uv pip install --python .venv pyinstaller

:: 一键打包（打包 -> 复制 config.json 进 dist -> 清理测试遗留）
.venv\Scripts\python.exe tools\build_exe.py
```

产物在 `dist\BiliLiveWatcher\`，整个文件夹就是分发单元：

| 文件 / 目录 | 说明 |
| --- | --- |
| BiliLiveWatcher.exe | 双击启动，全程无命令行窗口 |
| _internal\ | 运行时依赖与只读资源（app.ico / tray.png），勿单独移动 |
| config.json | 监控配置（可写，运行时读写在此处） |
| state.json / watcher.log | 首次运行自动生成 |

## 其它路线（如果以后想换）

-   **手机推送 / 电脑不用开**：GitHub Actions cron + Server酱 / Bark / Telegram Bot（延迟约 5 分钟起）。
-   **完全不写码**：B 站 App 关注 + 开播提醒，或 RSSHub + RSS 阅读器。

## 许可证

[MIT](LICENSE) © 2026 highwindmx

Powered by WorkBuddy

