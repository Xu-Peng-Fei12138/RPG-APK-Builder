# RPG-APK-Builder

一键把 RPG Maker MV / MZ 游戏打包成安卓 APK 的桌面工具环境（基于 [Yunbierdika/rpgmmv2android](https://github.com/Yunbierdika/rpgmmv2android) 扩展）。

**核心能力**

- 支持打包 **RPG Maker MV** 与 **RPG Maker MZ** 游戏
- **RPG Maker MZ 原生存档适配**（支持 `.rmmzsave` / pako 压缩格式，可正常读写 MZ 存档）
- 图形化一键打包工具：自动拷贝游戏资源、按引擎打补丁、生成签名、调用 Gradle 构建，最终输出 `Game.apk`
- 无需手动编辑 `build.gradle.kts`，也无需打开 Android Studio

> 本项目基于 [Yunbierdika/rpgmmv2android](https://github.com/Yunbierdika/rpgmmv2android)（MIT License）修改而来，原作者为 Yunbierdika，详见文末「捐赠」。

## 关于本项目

本项目在原作者基础上新增/调整了：

- 新增 RPG Maker MZ 存档适配（支持 `.rmmzsave` / pako 压缩格式）
- 新增图形化一键打包工具（`打包工具/`）
- 适配所需的其他配置（如 `applicationId`、`MainActivity` 的小幅 UI 修复等）

> 本项目保留原项目原作者的信息与捐赠入口见文末「捐赠」。

### 项目结构

本仓库是一次配置即可打包 RPG Maker MV / MZ 游戏的 Android 打包环境，三个目录分工如下：
<img width="569" height="112" alt="image" src="https://github.com/user-attachments/assets/908d33ad-5936-44d4-977e-b8e29038a967" />
- **`rpgmmv2android-main/`** —— Android 壳工程（基于原项目）。内含 WebView 加载游戏的 `assets` 目录，以及自动适配 MV/MZ 的存档逻辑。
- **`打包工具/`** —— 桌面打包工具。读取 `www/` 下的游戏资源，自动拷贝进壳工程的 `assets`、按引擎自动打补丁、生成签名、调用 Gradle 构建 APK。
- **`www/`** —— 游戏资源存放目录。将你的游戏发布包（`index.html`、`data/`、`js/`、`img/`、`audio/` 等）放入此目录即可（需要自行创建）。

### 使用方法（推荐：打包工具一键打包）

1. 将游戏资源（RPG Maker **MV 或 MZ** 游戏目录下的 `www` 文件夹内文件，不含 `www` 文件夹本身）放入本项目的 `www/` 目录。
2. 打开 `打包工具/start_builder.bat`（需已安装带 tkinter 的 Python 3 及 Java JDK）。
3. 在打包界面中：选择游戏来源（默认指向 `www/`）、填写 `applicationId` 与应用名、可选择自定义图标（未选则用 `www/icon/icon.png`，若无则使用默认图标）。
4. 点击打包。工具会自动：清空并重建壳工程的 `assets` → 写入游戏资源 → 按 MV/MZ 自动打存档适配补丁 → 自动生成签名密钥 → 调用 `gradlew` 构建。
5. 打包完成后，APK 输出到本项目根目录的 **`Game.apk`**。

> 相比原项目的手动流程，本工具已自动化了"复制资源进 assets、替换内核脚本、配置 applicationId、生成图标、签名"等步骤，无需手动编辑 `build.gradle.kts` 或使用 Android Studio。
>
> 若你仍希望用 Android Studio 手动构建，可按下方"原项目手动流程"操作。

### 原项目手动流程（可选）

1. 将游戏资源存放到 **_app/src/main/assets/_** 目录下（RPGMMV 游戏目录下的'www'文件夹内的文件，不包含'www'文件夹）后打包即可（ **注意不要替换掉本项目的中 js 文件夹中的文件** ）

2. **注意！！！**请将项目中的 **“rpg_managers.js”和“gameEnd.js”、“UTA_CommonSave.js”（如果游戏中原本有的话）** 这几个文件里的**代码**，注意，是**代码**，**不是整个文件**，复制**替换**掉你的 RPGMMV 游戏里**同文件名**的文件里的代码。

3. 将 **_app/src/build.gradle.kts_** 中的 **applicationId = "game.YourGameName"** 改成你自己的游戏名称（英文）比如：**my.xiaohuangyou**，用于显示路径包名；将 **_app/src/main/res/values/strings.xml_** 中的 **YourGameName** 改成你自己的游戏名称，用于显示 App 的名称。

4. 使用 Android Studio 打开项目，删除 **_app/src/main/res_** 目录下的 mipmap ，然后右键 **_app/src/main/res_** 点击 **“New -> Image Asset”** 添加你自己的游戏图标，图标的 **“Name”** 需要和 **_app/src/main/AndroidManifest.xml_** 中的 **“android:icon”**、**“android:roundIcon”** 一致。 最后编译即可。

### 特性

- 点击游戏中存档的保存按钮后，存档将直接保存到 **_Android/data/game.YourGameName/files/save/_** 目录下，并且存档可以和 PC 版互通。

- 不需要获取任何权限，非 ROOT 用户可以直接进入 **_Android/data/game.YourGameName/files/save/_** 目录下对存档进行导入、导出操作。

- 游戏出现错误时会把错误记录到 **_Android/data/game.YourGameName/files/log.txt_** 文件中。

- 游戏加载、保存时会将存档文件（除了global.rpgsave、config.rpgsave、common.rpgsave）缓存到 **_Android/data/game.YourGameName/cache/save/_** 目录下，下次进入游戏时，会优先从缓存中加载，提高加载、读档速度。

- 可以通过配置文件（**_Android/data/game.YourGameName/files/config.txt_**）设置 RPGMMV 使用 webgl 或 canvas；设置 WebView 使用硬件加速或软件加速，以便解决部分设备的图形显示问题。

### 捐赠

**原作者 Yunbierdika**：如果你受益于原项目，欢迎通过爱发电给原作者打赏：https://afdian.com/a/yun3812528

**本项目维护者**：如果你觉得本仓库的改动（如 MZ 适配）对你有帮助，也欢迎赞助支持：https://ifdian.net/a/XPF12138
