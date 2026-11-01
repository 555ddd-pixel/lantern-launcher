# 老张的minecraft启动器 1.8.1

这一版把「自动更新」这条链路的最后一处硬依赖去掉了。

## 修了什么

- **发版时忘了传 `update.json`，老用户也不会再被卡住。** 以前客户端只认
  `releases/latest/download/update.json`，这个附件不存在就直接 404，检查更新永远失败。
  现在清单拿不到时会自动退一步请求 GitHub API 的 `releases/latest`，把 tag 当版本号、
  把 `LanternLauncher.exe` 当下载地址，照样能升级。清单存在时依旧优先用清单（可校验 SHA1）。
- 检查更新的报错信息更实用了：每个源失败的具体原因都会摊开显示，不再是一句含糊的「未配置更新源」。
- 发布流程工具化：`python tools/release.py` 体检、`--publish` 一键发版，tag 名由代码里的
  `VERSION` 决定，不会再出现「tag 写 1.8.0、附件却是 1.8.1」这种错位。

## 包含之前未发布的改动

### 1.8.0
- 「模组」页新增**在线搜索下载**：直接搜 Modrinth 的模组 / 光影 / 资源包，按当前实例的
  MC 版本和加载器过滤，选好文件版本一键下载进目录；数据源与整合包共用（自动 / 官方 / 国内镜像可切）。

### 1.7.0
- 新增**模组 / 光影管理页**：`mods`、`shaderpacks`、`resourcepacks` 三个区域分开管理，
  单个开关启停（`x.jar` ↔ `x.jar.disabled`）、批量启停、删除、拖入导入。
- 下载时可选**装哪种加载器**：Fabric / Quilt / Forge / NeoForge，自动取对应的版本线。

### 1.6.0
- 整合包界面增加**国内镜像与加速代理**切换（官方 / 镜像 / 自动探测），解决国内网络打不开的问题。
- 启动器正式更名为「老张的minecraft启动器」。

## 升级方式

启动器里点「检查更新」即可，或直接下载下方的 `LanternLauncher.exe` 覆盖旧文件。
数据（`config.json`、`.minecraft`、`runtime`）都在 exe 旁边，覆盖不会丢。

## 校验

`update.json` 里的 SHA1 与附件一致，体积以附件实际大小为准。
