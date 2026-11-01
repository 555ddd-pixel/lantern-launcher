# 老张的minecraft启动器 🏮

[![Release](https://img.shields.io/github/v/release/555ddd-pixel/lantern-launcher?label=release)](https://github.com/555ddd-pixel/lantern-launcher/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-blue)](./README.md)

**下载**：到 [Releases](https://github.com/555ddd-pixel/lantern-launcher/releases/latest) 下载 `LanternLauncher.exe`，双击即用（免安装，Windows 10/11 自带 WebView2）。

> 显示名已改为**老张的minecraft启动器**（窗口标题 / 任务栏 / 托盘 / 界面 / 文件属性）；
> exe 文件名保留 ASCII 的 `LanternLauncher.exe`，这样老版本客户端的自动更新能原地替换、不会断链。

一个**独立实现**的 Minecraft: Java 版启动器，可以列出并下载 **任意官方版本**（正式版 / 快照 / 远古 Alpha·Beta），下载完整游戏文件并用正确的参数把游戏拉起来。

后端 Python 3 标准库（零第三方依赖），前端是本地 Web 界面，双击 `启动器.bat` 即可运行。

---

## 和 PCL2 的关系（重要）

PCL2（Plain Craft Launcher 2，作者 龙腾猫跃）是我这次调研的参考对象：

| 项目 | PCL2 | 本项目 |
| --- | --- | --- |
| 语言 / 框架 | VB.NET（新版社区版为 C# / .NET 10） | Python 3 + 原生 HTML/CSS/JS |
| 许可 | **自定义《PCL 分发有限许可》**（不是网上流传的 LGPL） | 本项目代码为独立实现，可自由修改 |
| 代码 | 仓库 `Hex-Dragon/PCL2` 只随版本发布同步 | 与本项目**无任何代码继承关系** |

它的许可把「参考较大量的内容，重新实现 PCL 的部分实质功能（例如启动或下载 Java 版 Minecraft）」定义为**重度使用**，要求改名并声明二次创作。所以这里**没有搬运任何 PCL2 代码**，只按 Minecraft 官方公开的启动协议（version manifest / version json / 下载源）从零实现，界面也是另起一套设计。PCL2 仍是那个更好用的启动器，本项目是学习性质的另一种实现。

---

## 功能

- **全版本下载**：921 个版本（103 正式版 / 757 快照 / 26 远古 Beta / 35 远古 Alpha），从 `rd-132211` 到最新快照
- **完整还原官方安装流程**：客户端 jar → libraries（含 rules 判定）→ natives 解压 → asset index → 数千个资源对象 → log4j 配置
- **多线程下载**：默认 16 并发，SHA1 校验、断点重下、原子写入
- **双下载源**：Mojang 官方 / BMCLAPI 镜像，支持自动测速选择
- **Java 智能匹配**：扫描本机所有 JVM，按版本要求（Java 8 / 17 / 21 / 25…）挑选；缺失时自动下载 Mojang 官方运行时（BMCLAPI 镜像，数秒即完成）
- **两种参数格式**：1.13+ 的 `arguments` 对象数组，以及 1.6–1.12 的 `minecraftArguments` 字符串，远古版本也能启动
- **登录分支**：登录窗口里先选「离线」或「微软正版」；正版点「开始登录」直接弹出微软官方登录页，登录并同意授权后启动器自动抓取回调、取回正版 ID 完成登录（内置公共客户端，无需自己申请 Azure 应用）
- **联机**：本机局域网地址一键复制；UPnP 端口映射把「对局域网开放」的端口开到公网；不支持 UPnP / 运营商内网时给出 P2P 组网方案（可一键下载 EasyTier 客户端）
- **整合包**：实时搜索网络整合包（Modrinth），一键下载 + 装原版 + 装 loader + 铺模组；也支持把 `.mrpack` / CurseForge `.zip` **拖进窗口**自动导入
- **整合包网络加速**：数据源可切「自动 / 官方 / 国内镜像（MCIM `mod.mcimirror.top`）」，官方超时自动换镜像；图标坏图自动改用镜像；还能填本机加速代理（如 Clash 的 `http://127.0.0.1:7890`），只作用于整合包请求，不影响 Mojang 下载
- **模组与光影管理**：独立的「模组」页按版本管理模组 / 光影 / 资源包——单个开关、一键全部启停、导入 / 拖拽安装（`.jar` 模组、`.zip` 光影 / 资源包）、删除进回收站（`.lantern-trash` 可找回）；自动读取 jar 里的模组名与版本，加载器不匹配会标提醒
- **选装加载器**：下载任意版本时可选 Fabric / Forge / NeoForge / Quilt 及其版本号，装好自动创建独立游戏目录（模组 / 光影 / 存档按版本隔离）；加载器版本列表官方源连不上时自动换 BMCLAPI 镜像
- **模组在线下载**：「模组」页一键打开在线商店，直接搜 Modrinth 上的模组 / 光影 / 资源包，自动按当前实例的 MC 版本和加载器过滤，选好文件版本一键下载进对应目录；数据源与整合包页共用（自动换官方 / 国内镜像、可走加速代理），空关键词显示热门内容，结果多可「加载更多」
- **老版本资源兼容**：自动处理 `virtual` / `map_to_resources` 的 pre-1.6 资源布局
- **超长 classpath 兜底**：超过 Windows 命令行 32K 上限时切换 Java 9+ 的 `@argfile`
- **桌面软件形态**：原生应用窗口（WebView2 内核，无地址栏）+ 系统托盘 + 记住窗口大小/位置 + 单实例唤起（第二次双击把窗口切到前台，不开新进程）
- **实时更新**（详见下文）：启动器自动检查/下载/替换自身并重启；实时监听 Minecraft 版本清单，出现新版本立刻弹提示
- **实时推送**：安装进度与游戏日志通过 SSE（Server-Sent Events）即时推送到界面，断线自动回退轮询
- 界面：版本筛选/搜索、实时进度与速度、启动日志、设置项即时保存

---

## 整合包

启动器内置整合包下载与导入，三种来源都能用：

| 来源 | 说明 |
| --- | --- |
| 「整合包」页实时搜索 | 边输入边搜 Modrinth（`project_type:modpack` 限定），可按 Fabric / Forge / Quilt / NeoForge 过滤 |
| 一键安装 | 选项目 → 选版本 → 自动装原版 → 装对应 loader → 下载所有模组 → 解压 overrides → 建实例 |
| 拖拽导入 | 把 `.mrpack` 或 CurseForge 导出的 `.zip` 拖进窗口任意位置，松手即开始导入 |

**实例隔离**：每个整合包装在 `<游戏目录>/instances/<名字>/` 里，`mods/`、`config/`、`saves/` 都在实例目录内，启动时用 `--gameDir` 指过去；原版、其他整合包的存档互不干扰。版本目录里会写一个 `.lantern-instance.json` 侧车文件记录映射，删版本时实例目录一起清掉。

**loader 安装方式**

- Fabric / Quilt：读官方 meta 的 `profile/json`，落成版本 JSON 后由启动器自己下依赖库（最稳）
- Forge / NeoForge：下载官方 installer jar，用 `java -jar ... --installClient <游戏目录>` 安装，之后再补齐库（需要本机有 Java）

**格式支持**

- `.mrpack`（Modrinth 官方格式）：完整支持，`modrinth.index.json` 里的文件都带下载地址
- CurseForge `.zip`：读 `manifest.json`，导入 `overrides/` 与包里自带的 mods；如果该包只写了 `projectID/fileID` 而没有实际 mods 文件，则补全需要 CurseForge API Key，日志里会明确提示

自检：`python tools/test_modpack.py`（解析真实 .mrpack、生成 Fabric 安装计划、跑一遍实例落盘并在启动参数里验证 `--gameDir`）

---

## 联机

「联机」页把三种情况的入口放在一起：

| 场景 | 做法 |
| --- | --- |
| 同一个 WiFi | 本机卡片里直接复制 `192.168.x.x:端口` 给朋友，游戏内「直接连接」 |
| 异地、路由器支持 UPnP | 填端口 → 「开放到公网」→ 复制 `公网IP:端口` 给朋友；点「关闭映射」会从路由器上撤掉 |
| 路由器没 UPnP / 运营商给内网地址 | 「生成房间」拿到网络名 + 密码，双方装 EasyTier（可点按钮自动下载），填一样的网络名和密码即可互相访问虚拟 IP |

实现细节：

- UPnP 走标准库：SSDP 组播找 IGD → 读设备描述 → SOAP `AddPortMapping` / `DeletePortMapping` / `GetExternalIPAddress`，不依赖任何第三方组件，也不需要管理员权限
- 发现顺序做了三层兜底：上次成功过的控制地址（缓存到 `.cache/upnp.json`）→ SSDP 组播 → 直接按常见地址（`rootDesc.xml` / `description.xml`）敲默认网关
- 公网地址落在 `100.64.0.0/10`（CGNAT）或 RFC1918 时直接判定「外面进不来」，把用户引到组网方案，而不是给一个假地址
- 访问本机与局域网（`127.0.0.1`、`192.168.x.x`）的请求强制绕开系统代理——用户开着代理软件时，`HTTP_PROXY` 会把这类请求也吞掉，单实例检测和 UPnP 都会失效
- 端口默认值会从最近一次游戏日志里抓「本地游戏已在端口 XXXX 上开启」

自检：`python tools/test_link.py`（起一个假路由器，跑通描述解析、公网地址、加/删映射、映射列表、CGNAT 识别、房间参数）

---

## 实时更新

启动器可以更新自己，整条链路是：**检查清单 → 后台下载（SHA1 校验）→ 改名交换替换 exe → 自动重启新版本**。

- 更新清单是一个 JSON（`update.json`），默认按顺序找：设置里的「更新清单地址」→ exe 同目录的 `update.json`
- 「更新清单地址」默认指向 `https://github.com/555ddd-pixel/lantern-launcher/releases/latest/download/update.json`，所以装过一次之后，只要仓库发了新 Release，客户端就会自己升级；取不到（离线/被墙）时自动回退到本地 `update.json`，不影响使用
- 清单里写新版本号、发布说明和各平台下载地址；发现更高版本号就提示更新
- 替换原理：Windows 允许重命名运行中的 exe，直接「旧 exe → .old 备份、新 exe 就位、启动新进程」，无需用户手动操作；失败时自动退回批处理脚本方案
- 界面上有三处提示：左侧栏更新徽标、主页顶部横幅（带下载进度条）、设置页的「启动器更新」面板
- 自动行为可在设置里调：自动检查开关、检查间隔、发现新版本是否自动下载
- 另外每分钟用 ETag 条件请求监听 Mojang 版本清单（304 无流量），**Minecraft 出新版本/新快照时界面立刻弹提示并刷新列表**

### 打包成 exe（桌面软件）

```bash
python build.py            # 生成 dist/LanternLauncher.exe + dist/update.json
python build.py --clean    # 清掉旧产物后重新打包
```

- 产物是**单文件绿色版**（约 15 MB，PyInstaller onefile，带图标与版本信息），免安装，数据（config.json / .minecraft / runtime）都放在 exe 旁边
- 双击 `LanternLauncher.exe` 直接弹出**软件窗口**（原生窗口，有标题栏/托盘，不是浏览器标签页）；再次双击不会起第二个实例，而是把已运行的窗口切到前台
- 窗口实现：默认用 pywebview + WebView2（Windows 10/11 自带运行时）；缺依赖时自动降级为 Edge/Chrome 内核的应用窗口（同样无地址栏），再不行就是普通浏览器页面
- 「窗口与托盘」设置：关闭窗口时最小化到托盘（点托盘图标恢复）、退出启动器
- **发布更新的方法**：改 `core/version.py` 里的 `VERSION` → `python build.py` → 到 [Releases](https://github.com/555ddd-pixel/lantern-launcher/releases/new) 建一个 `v1.3.1` 这样的 Tag，把 `dist/LanternLauncher.exe` 和 `dist/update.json` **两个都**作为附件上传 → 发布。老用户的启动器下一次检查就会自动完成升级（`build.py` 已把清单里的下载地址写成对应 Release 的绝对地址）
- 只想做离线/U 盘分发时用 `python build.py --local-url`，清单里的地址会退回相对路径（exe 和 update.json 放同一个文件夹即可）
- 命令行：`--browser`（用默认浏览器打开）、`--headless`（只起服务）、`--port 9000`、`--version`

---

## 使用

**推荐：直接用打包好的 exe** —— 双击 `dist/LanternLauncher.exe`，弹出软件窗口即用。

源码运行：

1. 需要 **Python 3.9+**（安装时勾选 Add to PATH）；窗口模式额外需要 `pip install pywebview pythonnet pystray`
2. 双击 `启动器.bat`（或 `python launcher.py`）
3. 软件窗口弹出；关闭窗口即退出
4. 在「下载」页挑版本 → 下载 → 「版本」页启动

首次启动会自动创建 `.minecraft` 游戏目录。

### 命令行

```bash
python launcher.py                # 启动软件窗口（默认）
python launcher.py --browser      # 用系统默认浏览器打开
python launcher.py --headless     # 只起本地服务，不开界面
python tools/selftest.py          # 解析自检：版本清单 / 库 / 资源
python tools/test_install.py 1.21.8 --launch   # 实测下载并启动
```

---

## 目录结构

```
lantern-launcher/
├─ 启动器.bat            双击入口（源码运行）
├─ launcher.py           入口：单实例检测 + 起本地服务
├─ build.py              一键打包成单文件 exe
├─ server.py             REST API + SSE 实时推送 + 静态文件
├─ core/
│  ├─ config.py          config.json 读写（便携式，全部数据在程序/exe 目录）
│  ├─ paths.py           源码/打包双模式路径解析（资源目录 vs 数据目录）
│  ├─ net.py             镜像改写 / 重试 / 下载校验 / 进度统计
│  ├─ manifest.py        版本清单、版本 JSON、本地版本扫描
│  ├─ live.py            实时监听 Minecraft 版本清单（ETag 条件请求）
│  ├─ meta.py            rules 判定、库与 natives 展开、参数占位符替换
│  ├─ installer.py       安装计划与并发下载
│  ├─ java.py            JVM 探测、版本匹配、Mojang 运行时下载
│  ├─ desktop.py         桌面窗口层：原生窗口 / 托盘 / 单实例唤起
│  ├─ auth.py            离线 UUID、微软 OAuth2 授权码登录与令牌链
│  ├─ mslogin.py         正版登录：开微软登录窗口、抓授权回调、自动收尾
│  ├─ content.py         模组 / 光影 / 资源包管理与加载器版本目录
│  ├─ launch.py          拼装 JVM/游戏参数并拉起进程
│  ├─ version.py         启动器自身版本号（发新版改这里）
│  └─ updater.py         实时自更新：检查 / 下载 / 改名交换替换 / 重启
├─ assets/               应用图标
├─ web/                  界面（index.html / styles.css / app.js）
├─ tools/                自检与实测脚本
├─ dist/                 打包产物（LanternLauncher.exe + update.json）
└─ .minecraft/           游戏目录（首次运行生成）
```

---

## 关键实现说明

**版本清单**：`piston-meta.mojang.com/mc/game/version_manifest_v2.json`，缓存 10 分钟。

**rules 判定**：官方语义是「默认禁止，逐条匹配取最后一条 action」；同时处理 `os.name`、`os.arch`、`os.version` 正则与 `features`。

**natives**：1.13 及更早的版本用 `natives.windows` 分类器 + `extract.exclude` 解压到 `versions/<id>/natives`；1.19+ 起 LWJGL natives jar 直接进 classpath，无需解压（这也是新版本 natives 数为 0 的原因）。

**资源**：`assets/objects/<前两位>/<hash>`，URL 固定为 `resources.download.minecraft.net/<前两位>/<hash>`。

**离线 UUID**：等价于 Java 的 `UUID.nameUUIDFromBytes("OfflinePlayer:" + name)`（MD5 + version 3 位）。

**Java 版本要求**：读取 version json 的 `javaVersion.majorVersion`；实际测试中 1.21.8 → 21、1.20.1 → 17、1.12.2 → 8、最新快照 → 25。

前 1.6 的版本使用 `assets: pre-1.6` 且资源索引带 `virtual: true`，启动器会把对象复制到 `assets/virtual/pre-1.6/`（或 `resources/`），否则游戏里没有声音和语言文件。

---

## 已知限制

- 只支持 Windows 桌面（Web 界面本身跨平台，但自动打开目录/进程窗口参数按 Windows 写的）
- 不提供 Forge / Fabric / NeoForge 安装器，只能启动已经装好的模组版本（`inheritsFrom` 合并已实现）
- 微软登录不会自动刷新 token，过期后需重新登录
- 未实现整合包（Modpack）导入、模组管理、皮肤上传
- 没有做多语言，界面为中文

---

## 免责声明

Minecraft 是 Mojang Studios / Microsoft 的商标，本项目与其无关联。请使用**已购买的正版账号**或离线模式进行个人学习测试；下载的游戏文件版权归 Mojang 所有。
