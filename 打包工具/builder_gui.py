#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RPGMMV -> Android APK 桌面 GUI 打包工具
基于 https://github.com/Yunbierdika/rpgmmv2android
使用 Python 内置 tkinter，无需额外安装
"""

import os, sys, re, shutil, time, subprocess, threading, queue

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError:
    print("错误: tkinter 未安装。这是 Python 标准库的一部分，请确保 Python 安装完整。")
    sys.exit(1)

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    print("错误: Pillow 未安装。请运行: pip install Pillow")
    sys.exit(1)

# ============================================================
# 配置
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
WWW_DIR = os.path.join(WORKSPACE_DIR, "www")
PROJECT_DIR = os.path.join(WORKSPACE_DIR, "rpgmmv2android-main")
ASSETS_DIR = os.path.join(PROJECT_DIR, "app", "src", "main", "assets")
KEYSTORE_PATH = os.path.join(PROJECT_DIR, "app", "release.jks")
KEYSTORE_ALIAS = "release"
# 签名密码禁止硬编码：优先环境变量，其次本地私密文件（不入库），最后随机生成
KEYSTORE_PASSWORD_FILE = os.path.join(BASE_DIR, "keystore.properties")

def _load_keystore_password():
    """安全获取签名密码，避免明文密码随源码发布到仓库。"""
    pw = os.environ.get("RPGMV_KEYSTORE_PASSWORD")
    if pw and pw.strip():
        return pw.strip()
    if os.path.exists(KEYSTORE_PASSWORD_FILE):
        with open(KEYSTORE_PASSWORD_FILE, "r", encoding="utf-8") as f:
            pw = f.read().strip()
        if pw:
            return pw
    import secrets as _secrets
    pw = _secrets.token_hex(16)
    try:
        with open(KEYSTORE_PASSWORD_FILE, "w", encoding="utf-8") as f:
            f.write(pw)
    except OSError:
        pass
    return pw

# pixi.js-legacy 内置资源路径（用于 MZ 游戏的 Canvas2D 回退）
PIXI_LEGACY_PATH = os.path.join(BASE_DIR, "pixi-legacy.min.js")

# ============================================================
# 文件过滤：自动排除 NW.js 运行时 / PC 专属 / 编辑器文件
# 这些文件只在 PC 端 NW.js 容器中使用，Android WebView 不会加载，
# 打包进 APK 只会无谓增大体积（通常可省 200MB+）。对 MV / MZ 均适用。
# ============================================================
EXCLUDE_DIRS = {
    "swiftshader",   # NW.js 软件渲染后端
    "locales",       # NW.js 多语言资源
    "Save",          # PC 端存档（Android 使用独立存档路径）
    "Dictionaries",  # 编辑器拼写检查字典
}

EXCLUDE_FILES = {
    # --- NW.js 运行时 ---
    "nw.dll", "nw_elf.dll", "nw.ini",
    "nw_100_percent.pak", "nw_200_percent.pak",
    "node.dll",
    "ffmpeg.dll", "ffmpeg.ini",
    "libEGL.dll", "libEGL", "libGLESv2.dll",
    "icudtl.dat",
    "resources.pak",
    "v8_context_snapshot.bin",
    "d3dcompiler_47.dll",
    # --- PC 启动器与工程文件 ---
    "Game.exe", "Game.rpgproject", "Game.user", "Game.ini",
    "notification_helper.exe",
    # --- NW.js credits ---
    "credits.html",
    # --- 已知的推广/广告文件（可按需增删）---
    "免责声明.txt",
    "扫码免费下载.png",
    "更多单机游戏COS写真免费下载.txt",
}

EXCLUDE_SUFFIXES = (
    ".url",   # Windows 快捷方式
    ".lnk",   # Windows 快捷方式
    ".bat",   # Windows 批处理脚本（非游戏资源）
)

# ============================================================
# 工具函数
# ============================================================
def log_to_queue(q, msg):
    """向队列发送日志消息"""
    timestamp = time.strftime("%H:%M:%S")
    q.put(f"[{timestamp}] {msg}\n")

def get_current_config():
    """读取当前项目的包名和 App 名称"""
    config = {"app_id": "", "app_name": ""}
    gradle_path = os.path.join(PROJECT_DIR, "app", "build.gradle.kts")
    if os.path.exists(gradle_path):
        with open(gradle_path, "r", encoding="utf-8") as f:
            m = re.search(r'applicationId\s*=\s*"([^"]+)"', f.read())
            if m:
                config["app_id"] = m.group(1)
    # 优先从 index.html 的 <title> 读取游戏名称
    index_path = os.path.join(WWW_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            m = re.search(r'<title>([^<]+)</title>', f.read())
            if m:
                config["app_name"] = m.group(1).strip()
    # 回退到 System.json 的 gameTitle（MZ 的标题存储位置）
    if not config["app_name"]:
        system_path = os.path.join(WWW_DIR, "data", "System.json")
        if os.path.exists(system_path):
            with open(system_path, "r", encoding="utf-8") as f:
                content = f.read()
            m = re.search(r'"gameTitle"\s*:\s*"([^"]*)"', content)
            if m and m.group(1).strip():
                config["app_name"] = m.group(1).strip()
    # 最后回退到 strings.xml
    if not config["app_name"]:
        strings_path = os.path.join(PROJECT_DIR, "app", "src", "main", "res", "values", "strings.xml")
        if os.path.exists(strings_path):
            with open(strings_path, "r", encoding="utf-8") as f:
                m = re.search(r'<string name="app_name">([^<]+)</string>', f.read())
                if m:
                    config["app_name"] = m.group(1)
    return config

# ============================================================
# 签名
# ============================================================
def ensure_keystore(log_queue):
    """检查 keystore 是否存在，不存在则自动生成"""
    if os.path.exists(KEYSTORE_PATH):
        log_to_queue(log_queue, "[签名] 使用已有签名文件")
        return True

    log_to_queue(log_queue, "[签名] 首次使用，自动生成签名文件...")
    try:
        keystore_password = _load_keystore_password()
        result = subprocess.run(
            [
                "keytool", "-genkeypair", "-v",
                "-keystore", KEYSTORE_PATH,
                "-keyalg", "RSA", "-keysize", "2048",
                "-validity", "9125",
                "-alias", KEYSTORE_ALIAS,
                "-storepass", keystore_password,
                "-keypass", keystore_password,
                "-dname", "CN=RPGMMV Builder, OU=Dev, O=RPGMMV, L=Unknown, ST=Unknown, C=CN",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            log_to_queue(log_queue, "[签名] 签名文件生成成功")
            return True
        else:
            log_to_queue(log_queue, f"[签名] 生成失败: {result.stderr}")
            return False
    except FileNotFoundError:
        log_to_queue(log_queue, "[签名] 错误: 未找到 keytool，请确保 JDK 已安装")
        return False
    except Exception as e:
        log_to_queue(log_queue, f"[签名] 异常: {e}")
        return False

def inject_signing_config(log_queue):
    """确保 build.gradle.kts 中包含签名配置"""
    gradle_path = os.path.join(PROJECT_DIR, "app", "build.gradle.kts")
    with open(gradle_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 如果已经有 signingConfigs 就跳过
    if "signingConfigs" in content and KEYSTORE_ALIAS in content:
        log_to_queue(log_queue, "[签名] 构建配置已包含签名信息")
        return

    # 在 buildTypes 之前插入 signingConfigs
    keystore_password = _load_keystore_password()
    signing_block = (
        '    signingConfigs {\n'
        f'        create("{KEYSTORE_ALIAS}") {{\n'
        '            storeFile = file("release.jks")\n'
        f'            storePassword = "{keystore_password}"\n'
        f'            keyAlias = "{KEYSTORE_ALIAS}"\n'
        f'            keyPassword = "{keystore_password}"\n'
        '        }\n'
        '    }\n\n'
    )

    # 在 buildTypes 前插入
    if "buildTypes {" in content:
        content = content.replace("    buildTypes {", signing_block + "    buildTypes {")
    else:
        log_to_queue(log_queue, "[签名] 警告: 未找到 buildTypes 块")
        return

    # 在 release buildType 中添加 signingConfig
    if "signingConfig" not in content:
        # 在 proguardFiles 后面、isMinifyEnabled 同级添加
        content = re.sub(
            r'(    release \{[^}]*?)(\n    \})',
            r'\1            signingConfig = signingConfigs.getByName("release")\n\2',
            content,
            count=1,
            flags=re.DOTALL,
        )

    with open(gradle_path, "w", encoding="utf-8") as f:
        f.write(content)
    log_to_queue(log_queue, "[签名] 构建配置已更新")

# ============================================================
# Node.js API 依赖检测
# ============================================================
def _needs_nodejs_polyfill(plugins_dir):
    """扫描插件目录，检测是否有插件使用了 Node.js API（require/nw/process）

    MZ 引擎本身在浏览器模式下不依赖这些 API（走 IndexedDB 存档），
    但部分第三方插件直接调用 require("fs")/require("path") 等。
    只有检测到使用时才注入 polyfill，避免影响不依赖 Node.js 的 MZ 游戏。
    """
    if not os.path.exists(plugins_dir):
        return False
    pattern = re.compile(
        r'require\s*\(|nw\.App|nw\.Window|process\.mainModule|process\.env|process\.platform'
    )
    for root, _, files in os.walk(plugins_dir):
        for f in files:
            if not f.endswith('.js'):
                continue
            try:
                with open(os.path.join(root, f), 'r', encoding='utf-8', errors='replace') as fh:
                    if pattern.search(fh.read()):
                        return True
            except OSError:
                pass
    return False

# ============================================================
# 核心：文件准备
# ============================================================
def prepare_files(app_id, app_name, icon_path, log_queue):
    """执行所有文件准备步骤"""
    log_to_queue(log_queue, "=== 开始文件准备 ===")

    # 1. 清理 assets
    log_to_queue(log_queue, "[1/8] 清理 assets 目录...")
    if os.path.exists(ASSETS_DIR):
        shutil.rmtree(ASSETS_DIR, ignore_errors=True)
        time.sleep(0.5)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    log_to_queue(log_queue, "完成")

    # 2. 复制游戏文件（自动过滤 NW.js 运行时与 PC 专属冗余文件）
    log_to_queue(log_queue, "[2/8] 复制游戏文件到 assets（自动过滤冗余）...")

    def _dir_size(path):
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    excluded_items = []

    for item in os.listdir(WWW_DIR):
        src = os.path.join(WWW_DIR, item)
        dst = os.path.join(ASSETS_DIR, item)

        if os.path.isdir(src):
            if item in EXCLUDE_DIRS:
                size = _dir_size(src)
                nfiles = sum(len(files) for _, _, files in os.walk(src))
                excluded_items.append((item + "/", f"{nfiles} 文件", size))
                continue
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            if item in EXCLUDE_FILES or item.lower().endswith(EXCLUDE_SUFFIXES):
                try:
                    size = os.path.getsize(src)
                except OSError:
                    size = 0
                excluded_items.append((item, "文件", size))
                continue
            shutil.copy2(src, dst)

    for name, kind, size in excluded_items:
        log_to_queue(log_queue, f"  排除: {name} ({kind}, {size / (1024*1024):.2f} MB)")

    count = sum(len(files) for _, _, files in os.walk(ASSETS_DIR))
    saved_mb = sum(s for _, _, s in excluded_items) / (1024 * 1024)
    log_to_queue(log_queue, f"完成（保留 {count} 个文件，排除 {len(excluded_items)} 项，节省 {saved_mb:.2f} MB）")

    # 3. 修改包名和 App 名称
    log_to_queue(log_queue, f"[3/8] 修改包名 -> {app_id}, App名称 -> {app_name}")
    gradle_path = os.path.join(PROJECT_DIR, "app", "build.gradle.kts")
    with open(gradle_path, "r", encoding="utf-8") as f:
        content = f.read()
    content = re.sub(r'applicationId\s*=\s*"[^"]*"', f'applicationId = "{app_id}"', content)
    with open(gradle_path, "w", encoding="utf-8") as f:
        f.write(content)

    strings_path = os.path.join(PROJECT_DIR, "app", "src", "main", "res", "values", "strings.xml")
    with open(strings_path, "r", encoding="utf-8") as f:
        content = f.read()
    content = re.sub(r'(<string name="app_name">)[^<]*(</string>)', rf'\g<1>{app_name}\g<2>', content)
    with open(strings_path, "w", encoding="utf-8") as f:
        f.write(content)
    log_to_queue(log_queue, "完成")

    # 3.5 签名配置
    log_to_queue(log_queue, "[签名] 检查签名配置...")
    ensure_keystore(log_queue)
    inject_signing_config(log_queue)

    # 4. 生成图标
    if icon_path and os.path.exists(icon_path):
        log_to_queue(log_queue, f"[4/8] 生成图标（来源: {os.path.basename(icon_path)}）")
        try:
            img = Image.open(icon_path).convert("RGBA")
            w, h = img.size

            # 创建正方形透明画布，边长取原图长边（避免任何裁剪）
            canvas_size = max(w, h)
            square = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
            offset_x = (canvas_size - w) // 2
            offset_y = (canvas_size - h) // 2
            square.paste(img, (offset_x, offset_y), img)

            res_dir = os.path.join(PROJECT_DIR, "app", "src", "main", "res")
            sizes = {
                "mipmap-mdpi": 48, "mipmap-hdpi": 72, "mipmap-xhdpi": 96,
                "mipmap-xxhdpi": 144, "mipmap-xxxhdpi": 192,
            }

            # 清理旧图标文件，避免 .png 与 .webp 冲突
            for folder, _ in sizes.items():
                out_dir = os.path.join(res_dir, folder)
                if os.path.exists(out_dir):
                    for f in os.listdir(out_dir):
                        if f.startswith("icon_launcher"):
                            os.remove(os.path.join(out_dir, f))

            generated = 0
            for folder, size in sizes.items():
                out_dir = os.path.join(res_dir, folder)
                os.makedirs(out_dir, exist_ok=True)
                resized = square.resize((size, size), Image.LANCZOS)

                # 方形图标：保持透明背景
                resized.save(os.path.join(out_dir, "icon_launcher.png"), "PNG")

                # 圆形图标：基于透明画布生成
                mask = Image.new("L", (size, size), 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0, size, size), fill=255)
                circular = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                circular.paste(resized, (0, 0), mask)
                circular.save(os.path.join(out_dir, "icon_launcher_round.png"), "PNG")
                circular.save(os.path.join(out_dir, "icon_launcher_foreground.png"), "PNG")
                generated += 3

            # 清理自适应图标 XML（禁用自适应图标，避免系统裁剪内容）
            anydpi = os.path.join(res_dir, "mipmap-anydpi-v26")
            if os.path.exists(anydpi):
                for f in os.listdir(anydpi):
                    if f.startswith("icon_launcher"):
                        os.remove(os.path.join(anydpi, f))

            log_to_queue(log_queue, f"完成（{generated} 个图标）")
        except Exception as e:
            log_to_queue(log_queue, f"图标生成失败: {e}")
    else:
        log_to_queue(log_queue, "[4/8] 跳过图标生成（未提供图标文件）")

    # 5. 修补 JS（兼容 RPG Maker MV 和 MZ）
    mv_path = os.path.join(ASSETS_DIR, "js", "rpg_managers.js")
    mz_path = os.path.join(ASSETS_DIR, "js", "rmmz_managers.js")

    if os.path.exists(mv_path):
        log_to_queue(log_queue, "[5/8] 检测到 RPG Maker MV，修补 rpg_managers.js...")
        with open(mv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        replacements = {
            "saveToWebStorage": (
                "StorageManager.saveToWebStorage = function(savefileId, json) {\n"
                "    var name = savefileId < 0 ? 'config.rpgsave' : savefileId === 0 ? 'global.rpgsave' : 'file' + savefileId + '.rpgsave';\n"
                "    AndroidBridge.saveGameData(json, name);\n"
                "};"
            ),
            "loadFromWebStorage": (
                "StorageManager.loadFromWebStorage = function(savefileId) {\n"
                "    var name = savefileId < 0 ? 'config.rpgsave' : savefileId === 0 ? 'global.rpgsave' : 'file' + savefileId + '.rpgsave';\n"
                "    return AndroidBridge.loadGameData(name);\n"
                "};"
            ),
            "webStorageExists": (
                "StorageManager.webStorageExists = function(savefileId) {\n"
                "    var name = savefileId < 0 ? 'config.rpgsave' : savefileId === 0 ? 'global.rpgsave' : 'file' + savefileId + '.rpgsave';\n"
                "    return AndroidBridge.existsGameSave(name);\n"
                "};"
            ),
        }

        new_lines = []
        i = 0
        patched = []
        while i < len(lines):
            matched = False
            for func_name, new_body in replacements.items():
                pattern = f"StorageManager.{func_name} = function("
                if pattern in lines[i]:
                    brace_count = 0
                    j = i
                    while j < len(lines):
                        brace_count += lines[j].count("{") - lines[j].count("}")
                        if brace_count <= 0 and "{" in "".join(lines[i:j+1]):
                            j += 1
                            break
                        j += 1
                    new_lines.append(new_body + "\n")
                    patched.append(func_name)
                    i = j
                    matched = True
                    break
            if not matched:
                new_lines.append(lines[i])
                i += 1

        with open(mv_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        log_to_queue(log_queue, f"完成 (已修补: {', '.join(patched)})")

        # --- 修补 rpg_core.js：添加 broken image 防护 ---
        mv_core_path = os.path.join(ASSETS_DIR, "js", "rpg_core.js")
        if os.path.exists(mv_core_path):
            log_to_queue(log_queue, "[5/8] 修补 rpg_core.js（broken image 防护）...")
            with open(mv_core_path, "r", encoding="utf-8") as f:
                core_content = f.read()

            core_patched = []

            # 1. _onLoad 中添加 broken image 检查
            old_onload = "Bitmap.prototype._onLoad = function() {\n    this._image.removeEventListener('load', this._loadListener);\n    this._image.removeEventListener('error', this._errorListener);\n\n    this._renewCanvas();"
            new_onload = "Bitmap.prototype._onLoad = function() {\n    this._image.removeEventListener('load', this._loadListener);\n    this._image.removeEventListener('error', this._errorListener);\n\n    // [Android Patch] 防护：图片触发load事件但实际处于broken状态\n    if (this._image.complete && this._image.naturalWidth === 0) {\n        this._loadingState = 'error';\n        console.warn('Bitmap._onLoad: image is broken:', this._url);\n        return;\n    }\n\n    this._renewCanvas();"
            if old_onload in core_content and "Android Patch" not in core_content:
                core_content = core_content.replace(old_onload, new_onload)
                core_patched.append("_onLoad broken防护")

            # 2. _renewCanvas 中添加 broken image 防护
            old_renew = "        this.__context.drawImage(this._image, 0, 0);\n    }\n\n    this._setDirty();\n};\n\nBitmap.prototype._createBaseTexture"
            new_renew = "        // [Android Patch] 防护：图片处于broken状态时跳过drawImage\n        if (this._image.complete && this._image.naturalWidth > 0) {\n            this.__context.drawImage(this._image, 0, 0);\n        }\n    }\n\n    this._setDirty();\n};\n\nBitmap.prototype._createBaseTexture"
            if old_renew in core_content and "Android Patch] 防护：图片处于broken状态时跳过drawImage" not in core_content:
                core_content = core_content.replace(old_renew, new_renew)
                core_patched.append("_renewCanvas broken防护")

            # 3. bltImage 中添加 broken image 防护
            old_blt = "        this._context.globalCompositeOperation = 'source-over';\n        this._context.drawImage(source._image, sx, sy, sw, sh, dx, dy, dw, dh);\n        this._setDirty();\n    }\n};\n\n/**\n * Returns pixel color"
            new_blt = "        // [Android Patch] 防护：检查source._image是否处于broken状态\n        if (source._image && source._image.complete && source._image.naturalWidth === 0) {\n            return;\n        }\n        this._context.globalCompositeOperation = 'source-over';\n        this._context.drawImage(source._image, sx, sy, sw, sh, dx, dy, dw, dh);\n        this._setDirty();\n    }\n};\n\n/**\n * Returns pixel color"
            if old_blt in core_content and "Android Patch] 防护：检查source._image" not in core_content:
                core_content = core_content.replace(old_blt, new_blt)
                core_patched.append("bltImage broken防护")

            if core_patched:
                with open(mv_core_path, "w", encoding="utf-8") as f:
                    f.write(core_content)
                log_to_queue(log_queue, f"完成 (已修补: {', '.join(core_patched)})")
            else:
                log_to_queue(log_queue, "跳过 (已包含防护或模式不匹配)")

    elif os.path.exists(mz_path):
        log_to_queue(log_queue, "[5/8] 检测到 RPG Maker MZ，修补 rmmz_managers.js...")
        with open(mz_path, "r", encoding="utf-8") as f:
            content = f.read()

        patch_code = """
// === Android Bridge 覆盖（由打包工具注入）===
StorageManager._androidExt = ".rmmzsave";

StorageManager.saveZip = function(saveName, zip) {
    // zip 是 pako.deflate(json, {to:"string"}) 返回的二进制字符串
    // 每个字符 charCode 在 0-255 范围内，btoa 可直接处理
    try {
        var base64 = btoa(zip);
        AndroidBridge.saveGameData(base64, saveName + this._androidExt);
        console.log('[Android/Storage] saveZip 成功:', saveName, 'base64长度:', base64.length);
        return Promise.resolve();
    } catch (e) {
        console.error('[Android/Storage] saveZip 失败:', saveName, e.message);
        return Promise.reject(e);
    }
};

StorageManager.loadZip = function(saveName) {
    var base64 = AndroidBridge.loadGameData(saveName + this._androidExt);
    if (!base64) {
        console.log('[Android/Storage] loadZip: 存档不存在或为空:', saveName);
        return Promise.reject(new Error('Savefile not found'));
    }
    try {
        var binary = atob(base64);
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        console.log('[Android/Storage] loadZip 成功:', saveName, 'base64长度:', base64.length, '字节长度:', bytes.length);
        return Promise.resolve(bytes);
    } catch (e) {
        console.error('[Android/Storage] loadZip atob 解码失败:', saveName, e.message);
        return Promise.reject(new Error('Savefile corrupted'));
    }
};

StorageManager.exists = function(saveName) {
    return AndroidBridge.existsGameSave(saveName + this._androidExt);
};

StorageManager.remove = function(saveName) {
    // 写入空字符串标记删除（loadZip 中空字符串视为不存在）
    AndroidBridge.saveGameData('', saveName + this._androidExt);
};
"""

        if "Android Bridge" in content:
            log_to_queue(log_queue, "[5/8] rmmz_managers.js 已有 AndroidBridge 补丁，跳过")
        else:
            content += patch_code
            with open(mz_path, "w", encoding="utf-8") as f:
                f.write(content)
            log_to_queue(log_queue, "完成 (MZ 已修补: saveZip, loadZip, exists, remove)")

        # --- 替换 pixi.js 为 pixi.js-legacy（支持 Canvas2D 回退）---
        assets_pixi = os.path.join(ASSETS_DIR, "js", "libs", "pixi.js")
        if os.path.exists(assets_pixi) and os.path.exists(PIXI_LEGACY_PATH):
            with open(assets_pixi, "r", encoding="utf-8") as f:
                head = f.read(50)
            if "pixi.js-legacy" not in head:
                shutil.copy2(PIXI_LEGACY_PATH, assets_pixi)
                log_to_queue(log_queue, "[5/8] 已替换 pixi.js -> pixi.js-legacy（Canvas2D 回退支持）")
            else:
                log_to_queue(log_queue, "[5/8] pixi.js 已是 legacy 版本，跳过")
        elif not os.path.exists(PIXI_LEGACY_PATH):
            log_to_queue(log_queue, "[5/8] 警告: 未找到 pixi-legacy.min.js，跳过替换")

        # --- 按需注入 require polyfill ---
        plugins_scan_dir = os.path.join(ASSETS_DIR, "js", "plugins")
        needs_polyfill = _needs_nodejs_polyfill(plugins_scan_dir)
        if needs_polyfill:
            log_to_queue(log_queue, "[5/8] 检测到插件使用 Node.js API，准备注入 polyfill...")
        else:
            log_to_queue(log_queue, "[5/8] 未检测到插件使用 Node.js API，跳过 polyfill 注入")

        main_js_path = os.path.join(ASSETS_DIR, "js", "main.js")
        if needs_polyfill and os.path.exists(main_js_path):
            with open(main_js_path, "r", encoding="utf-8") as f:
                main_content = f.read()
            if "require polyfill" not in main_content:
                polyfill = (
                    '// [Android/Compat] require polyfill\n'
                    '// process polyfill\n'
                    'if (typeof process === "undefined") {\n'
                    '    window.process = {\n'
                    '        mainModule: { filename: window.location.href.replace(/\\/[^\\/]*$/, "/index.html") },\n'
                    '        env: {},\n'
                    '        platform: "browser",\n'
                    '        versions: {}\n'
                    '    };\n'
                    '}\n'
                    'if (typeof require === "undefined") {\n'
                    '    window.require = function(m) {\n'
                    '        switch (m) {\n'
                    '            case "fs":\n'
                    '                return {\n'
                    '                    existsSync: function(p) {\n'
                    '                        if (typeof AndroidBridge !== "undefined" && AndroidBridge.existsGameSave) {\n'
                    '                            return AndroidBridge.existsGameSave(p.split(/[\\/]/).pop());\n'
                    '                        }\n'
                    '                        return false;\n'
                    '                    },\n'
                    '                    readFileSync: function(p, o) {\n'
                    '                        if (typeof AndroidBridge !== "undefined" && AndroidBridge.loadGameData) {\n'
                    '                            var d = AndroidBridge.loadGameData(p.split(/[\\/]/).pop());\n'
                    '                            if (d === null) throw new Error("File not found: " + p);\n'
                    '                            return d;\n'
                    '                        }\n'
                    '                        throw new Error("fs.readFileSync not available");\n'
                    '                    },\n'
                    '                    writeFileSync: function(p, d) {\n'
                    '                        if (typeof AndroidBridge !== "undefined" && AndroidBridge.saveGameData) {\n'
                    '                            AndroidBridge.saveGameData(d, p.split(/[\\/]/).pop());\n'
                    '                        }\n'
                    '                    },\n'
                    '                    mkdirSync: function() {},\n'
                    '                    unlinkSync: function() {},\n'
                    '                    renameSync: function() {},\n'
                    '                };\n'
                    '            case "path":\n'
                    '                return {\n'
                    '                    join: function() { return Array.prototype.slice.call(arguments).join("/").replace(/\\/+/g, "/"); },\n'
                    '                    dirname: function(p) { return p.replace(/\\\\/g, "/").replace(/\\/[^\\/]*$/, ""); },\n'
                    '                    basename: function(p) { return p.replace(/\\\\/g, "/").split("/").pop(); },\n'
                    '                    extname: function(p) { var i = p.lastIndexOf("."); return i >= 0 ? p.substring(i) : ""; },\n'
                    '                };\n'
                    '            case "nw.gui":\n'
                    '                return { Window: { get: function() { return { showDevTools: function() {} }; } } };\n'
                    '            default: return {};\n'
                    '        }\n'
                    '    };\n'
                    '}\n'
                )
                main_content = polyfill + "\n" + main_content
                with open(main_js_path, "w", encoding="utf-8") as f:
                    f.write(main_content)
                log_to_queue(log_queue, "[5/8] 已注入 require polyfill 到 main.js")
            else:
                log_to_queue(log_queue, "[5/8] main.js 已有 require polyfill，跳过")

        # --- 额外修补 rmmz_core.js 和 rmmz_managers.js：修复图形初始化 ---
        core_path = os.path.join(ASSETS_DIR, "js", "rmmz_core.js")
        if os.path.exists(core_path):
            log_to_queue(log_queue, "[5/8] 修补 rmmz_core.js（图形初始化兼容）...")
            with open(core_path, "r", encoding="utf-8") as f:
                core_content = f.read()

            patched_core = False

            # 修复1: canvas 初始尺寸 0 -> 816x624
            if "this._width = 0;\n    this._height = 0;" in core_content and "Android Graphics Fix" not in core_content:
                core_content = core_content.replace(
                    "this._width = 0;\n    this._height = 0;",
                    "// [Android/Graphics Fix] 避免 canvas 尺寸为 0 导致 WebGL 初始化失败\n"
                    "    this._width = 816;\n    this._height = 624;",
                    1
                )
                patched_core = True

            # 修复2: _createPixiApp 增加 Canvas2D 自动回退
            old_pixi = (
                "    } catch (e) {\n"
                "        this._app = null;\n"
                "    }\n"
                "};"
            )
            new_pixi = (
                "    } catch (e) {\n"
                "        // [Android/Graphics Fix] WebGL 不可用时回退到 Canvas2D 渲染\n"
                "        console.warn('[Graphics] WebGL 渲染失败，回退到 Canvas2D:', e.message);\n"
                "        try {\n"
                "            this._setupPixi();\n"
                "            this._app = new PIXI.Application({\n"
                "                view: this._canvas,\n"
                "                autoStart: false,\n"
                "                forceCanvas: true\n"
                "            });\n"
                "            this._app.ticker.remove(this._app.render, this._app);\n"
                "            this._app.ticker.add(this._onTick, this);\n"
                "            console.info('[Graphics] Canvas2D 渲染已启用');\n"
                "        } catch (e2) {\n"
                "            console.error('[Graphics] Canvas2D 渲染也失败:', e2);\n"
                "            this._app = null;\n"
                "        }\n"
                "    }\n"
                "};"
            )
            if old_pixi in core_content and "forceCanvas" not in core_content:
                core_content = core_content.replace(old_pixi, new_pixi, 1)
                patched_core = True

            # 修复3: 覆盖 _setupPixi，让 PIXI Renderer.create 支持 Canvas2D 回退
            old_setup = (
                "Graphics._setupPixi = function() {\n"
                "    PIXI.utils.skipHello();\n"
                "    PIXI.settings.GC_MAX_IDLE = 600;\n"
                "};"
            )
            new_setup = (
                "Graphics._setupPixi = function() {\n"
                "    PIXI.utils.skipHello();\n"
                "    PIXI.settings.GC_MAX_IDLE = 600;\n"
                "\n"
                "    // [Android/Graphics Fix] 覆盖 PIXI Renderer.create，\n"
                "    // 在 WebGL 不可用时自动回退到 CanvasRenderer（Canvas2D 渲染）\n"
                "    if (PIXI.CanvasRenderer && !PIXI.Renderer._androidPatched) {\n"
                "        var OriginalRenderer = PIXI.Renderer;\n"
                "        var CanvasRenderer = PIXI.CanvasRenderer;\n"
                "        OriginalRenderer._androidPatched = true;\n"
                "        OriginalRenderer.create = function(options) {\n"
                "            try {\n"
                "                return new OriginalRenderer(options);\n"
                "            } catch (e) {\n"
                "                console.warn('[Graphics] WebGL 不可用，回退到 Canvas2D:', e.message);\n"
                "                return new CanvasRenderer(options);\n"
                "            }\n"
                "        };\n"
                "    }\n"
                "};"
            )
            if old_setup in core_content and "_androidPatched" not in core_content:
                core_content = core_content.replace(old_setup, new_setup, 1)
                patched_core = True

            # 修复4: Utils.isNwjs 增加 nw 检查
            old_isnwjs = (
                'Utils.isNwjs = function() {\n'
                '    return typeof require === "function" && typeof process === "object";\n'
                '};'
            )
            new_isnwjs = (
                'Utils.isNwjs = function() {\n'
                '    // [Android/Compat] 增加 nw 检查：polyfill 注入了 require/process，\n'
                '    // 但浏览器/WebView 没有 nw 对象，不应误判为 NW.js 环境\n'
                '    return typeof require === "function" && typeof process === "object" && typeof nw !== "undefined";\n'
                '};'
            )
            if needs_polyfill and old_isnwjs in core_content and "Android/Compat" not in core_content:
                core_content = core_content.replace(old_isnwjs, new_isnwjs, 1)
                patched_core = True

            if patched_core:
                with open(core_path, "w", encoding="utf-8") as f:
                    f.write(core_content)
                log_to_queue(log_queue, "完成 (rmmz_core.js: canvas尺寸 + Canvas2D回退)")
            elif "Android Graphics Fix" in core_content or "forceCanvas" in core_content:
                log_to_queue(log_queue, "[5/8] rmmz_core.js 已有图形修复补丁，跳过")
            else:
                log_to_queue(log_queue, "[5/8] 警告: rmmz_core.js 未找到目标代码，跳过图形修补")

        # 修复3: 移除 SceneManager.checkBrowser 中的 WebGL 强制检查
        managers_path = os.path.join(ASSETS_DIR, "js", "rmmz_managers.js")
        if os.path.exists(managers_path):
            with open(managers_path, "r", encoding="utf-8") as f:
                mgr_content = f.read()

            old_check = (
                'SceneManager.checkBrowser = function() {\n'
                '    if (!Utils.canUseWebGL()) {\n'
                '        throw new Error("Your browser does not support WebGL.");\n'
                '    }\n'
            )
            new_check = (
                'SceneManager.checkBrowser = function() {\n'
                '    // [Android/Graphics Fix] 不再强制要求 WebGL，\n'
                '    // Graphics._createPixiApp 已支持 Canvas2D 自动回退\n'
            )
            if old_check in mgr_content and "Canvas2D 自动回退" not in mgr_content:
                mgr_content = mgr_content.replace(old_check, new_check, 1)
                with open(managers_path, "w", encoding="utf-8") as f:
                    f.write(mgr_content)
                log_to_queue(log_queue, "完成 (rmmz_managers.js: 移除 WebGL 强制检查)")

        # 修复4: Window_Base.initialize 对无效 rect 参数的回退处理
        windows_path = os.path.join(ASSETS_DIR, "js", "rmmz_windows.js")
        if os.path.exists(windows_path):
            with open(windows_path, "r", encoding="utf-8") as f:
                win_content = f.read()

            old_win_init = (
                'Window_Base.prototype.initialize = function(rect) {\n'
                '    Window.prototype.initialize.call(this);\n'
                '    this.loadWindowskin();\n'
                '    this.checkRectObject(rect);\n'
                '    this.move(rect.x, rect.y, rect.width, rect.height);\n'
            )
            new_win_init = (
                'Window_Base.prototype.initialize = function(rect) {\n'
                '    Window.prototype.initialize.call(this);\n'
                '    this.loadWindowskin();\n'
                '    // [Android/Compat] 当 rect 无效时使用默认矩形，避免崩溃\n'
                '    if (typeof rect !== "object" || typeof rect.x !== "number") {\n'
                '        rect = new Rectangle(0, 0, Graphics.boxWidth, Graphics.boxHeight);\n'
                '    }\n'
                '    this.move(rect.x, rect.y, rect.width, rect.height);\n'
            )
            if old_win_init in win_content and "Android/Compat" not in win_content:
                win_content = win_content.replace(old_win_init, new_win_init, 1)
                with open(windows_path, "w", encoding="utf-8") as f:
                    f.write(win_content)
                log_to_queue(log_queue, "完成 (rmmz_windows.js: rect 参数回退)")

    else:
        log_to_queue(log_queue, "[5/8] 警告: 未找到 rpg_managers.js 或 rmmz_managers.js")

    # 6. 添加插件
    log_to_queue(log_queue, "[6/8] 添加 gameEnd.js + UTA_CommonSave.js...")
    plugins_dir = os.path.join(ASSETS_DIR, "js", "plugins")
    os.makedirs(plugins_dir, exist_ok=True)

    with open(os.path.join(plugins_dir, "gameEnd.js"), "w", encoding="utf-8") as f:
        f.write(""";(function () {
  var parameters = PluginManager.parameters('gameEnd')
  var EndName = String(parameters['endName'] || '\u30b2\u30fc\u30e0\u7d42\u4e86')
  Window_TitleCommand.prototype.makeCommandList = function () {
    this.addCommand(TextManager.newGame, 'newGame')
    this.addCommand(TextManager.continue_, 'continue', this.isContinueEnabled())
    this.addCommand(EndName, 'gameEnd')
  }
  Scene_Title.prototype.commandGameEnd = function () {
    if (StorageManager.isLocalMode()) { window.close() }
    else { window.close(); AndroidBridge.closeGame() }
  }
})()
""")

    with open(os.path.join(plugins_dir, "UTA_CommonSave.js"), "w", encoding="utf-8") as f:
        f.write("""StorageManager.loadFromWebStorageCommonSave = function () {
  return AndroidBridge.loadGameData('common.rpgsave')
}
StorageManager.saveToWebStorageCommonSave = function (json) {
  AndroidBridge.saveGameData(json, 'common.rpgsave')
}
StorageManager.webStorageExistsCommonSave = function () {
  return AndroidBridge.existsGameSave('common.rpgsave')
}
StorageManager.removeWebStorageCommonSave = function () {
  AndroidBridge.removeCommonSave()
}
""")
    log_to_queue(log_queue, "完成")

    # 7. 注册插件
    log_to_queue(log_queue, "[7/8] 更新 plugins.js...")
    fp2 = os.path.join(ASSETS_DIR, "js", "plugins.js")
    with open(fp2, "r", encoding="utf-8") as f:
        content = f.read()
    gameend_entry = '{"name":"gameEnd","status":true,"description":"game end plugin","parameters":{"endName":"\\u30b2\\u30fc\\u30e0\\u7d42\\u4e86"}}'
    uta_entry = '{"name":"UTA_CommonSave","status":true,"description":"CommonSave Android Bridge","parameters":{}}'
    if "}] ;" in content:
        content = content.replace("}] ;", f"}}, {gameend_entry}, {uta_entry}];", 1)
    elif "}];" in content:
        content = content.replace("}];", f"}}, {gameend_entry}, {uta_entry}];", 1)
    with open(fp2, "w", encoding="utf-8") as f:
        f.write(content)
    log_to_queue(log_queue, "完成")

    # 8. 修复损坏的图标
    log_to_queue(log_queue, "[8/8] 检查图标完整性...")
    res_dir = os.path.join(PROJECT_DIR, "app", "src", "main", "res")
    for d in ["mipmap-hdpi", "mipmap-mdpi", "mipmap-xhdpi", "mipmap-xxhdpi", "mipmap-xxxhdpi"]:
        src = os.path.join(res_dir, d, "icon_launcher.png")
        dst = os.path.join(res_dir, d, "icon_launcher_foreground.png")
        if os.path.exists(src):
            try:
                with open(dst, "rb") as f:
                    f.read(1)
            except:
                shutil.copy2(src, dst)
    log_to_queue(log_queue, "完成")
    log_to_queue(log_queue, "=== 文件准备全部完成 ===")

# ============================================================
# Gradle 构建
# ============================================================
def run_gradle_build(variant, log_queue):
    """运行 Gradle 构建，流式输出日志"""
    gradle_bat = os.path.join(PROJECT_DIR, "gradlew.bat")
    if not os.path.exists(gradle_bat):
        log_to_queue(log_queue, "错误: 未找到 gradlew.bat")
        return False

    cmd = [gradle_bat, "--no-daemon", "clean", f"assemble{variant}"]
    log_to_queue(log_queue, f"执行: {' '.join(cmd)}")

    try:
        process = subprocess.Popen(
            cmd, cwd=PROJECT_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        for line in process.stdout:
            line = line.rstrip()
            if line.strip():
                log_to_queue(log_queue, line)
        process.wait()
        success = process.returncode == 0

        if success:
            apk_dir = os.path.join(PROJECT_DIR, "app", "build", "outputs", "apk", variant)
            if os.path.exists(apk_dir):
                for f in os.listdir(apk_dir):
                    if f.endswith(".apk"):
                        src = os.path.join(apk_dir, f)
                        dst = os.path.join(WORKSPACE_DIR, "Game.apk")
                        shutil.copy2(src, dst)
                        size_mb = os.path.getsize(dst) / (1024 * 1024)
                        log_to_queue(log_queue, f"=== APK 打包成功！输出: Game.apk ({size_mb:.2f} MB) ===")
                        return True
            log_to_queue(log_queue, "警告: 构建成功但未找到 APK 文件")
            return True
        else:
            log_to_queue(log_queue, f"=== Gradle 构建失败 (exit code: {process.returncode}) ===")
            log_to_queue(log_queue, "常见原因: 1) Windows Defender 干扰 2) 编译缓存损坏 3) SDK 版本不匹配")
            return False
    except Exception as e:
        log_to_queue(log_queue, f"构建异常: {e}")
        return False

# ============================================================
# GUI 类
# ============================================================
class BuilderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("RPGMMV APK 打包工具")
        self.root.geometry("760x820")
        self.root.configure(bg="#1a1d27")
        self.root.resizable(False, False)

        # 图标路径
        self.icon_path = ""
        self.log_queue = queue.Queue()

        # 样式
        self.bg = "#1a1d27"
        self.bg2 = "#242836"
        self.bg3 = "#0f1117"
        self.ink = "#e8eaed"
        self.muted = "#8b8fa3"
        self.accent = "#4fc3f7"
        self.accent2 = "#00e676"
        self.danger = "#ff5252"
        self.rule = "#2e3345"

        self.build_font = ("Microsoft YaHei UI", 10)
        self.title_font = ("Microsoft YaHei UI", 16, "bold")
        self.label_font = ("Microsoft YaHei UI", 9)

        self.create_widgets()
        self.load_defaults()
        self.poll_log()

    def create_widgets(self):
        r = self.root

        # 标题
        tk.Label(r, text="RPGMMV -> Android APK", font=self.title_font,
                 bg=self.bg, fg=self.accent).pack(pady=(20, 4))
        tk.Label(r, text="基于 rpgmmv2android 项目 · 一键打包",
                 font=self.label_font, bg=self.bg, fg=self.muted).pack()

        # === 主内容区域 ===
        frame = tk.Frame(r, bg=self.bg)
        frame.pack(padx=24, pady=16, fill=tk.BOTH, expand=True)

        # 1. 包名
        self._make_section(frame, "1", "包名 (applicationId)", 0)
        tk.Label(frame, text="Android 应用的唯一标识符", font=self.label_font,
                 bg=self.bg, fg=self.muted).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.entry_appid = tk.Entry(frame, font=self.build_font, bg=self.bg3, fg=self.ink,
                                    insertbackground=self.ink, relief=tk.FLAT, width=50)
        self.entry_appid.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        tk.Label(frame, text="影响存档路径: Android/data/此包名/files/save/", font=self.label_font,
                 bg=self.bg, fg=self.muted).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 12))

        # 2. App 名称
        self._make_section(frame, "2", "App 名称", 4)
        tk.Label(frame, text="手机桌面和应用管理中显示的名称", font=self.label_font,
                 bg=self.bg, fg=self.muted).grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.entry_appname = tk.Entry(frame, font=self.build_font, bg=self.bg3, fg=self.ink,
                                      insertbackground=self.ink, relief=tk.FLAT, width=50)
        self.entry_appname.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(4, 12))

        # 3. 图标
        self._make_section(frame, "3", "应用图标", 7)
        icon_frame = tk.Frame(frame, bg=self.bg)
        icon_frame.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(4, 12))

        # 图标预览框
        self.icon_canvas = tk.Canvas(icon_frame, width=80, height=80, bg=self.bg3,
                                     highlightthickness=1, highlightbackground=self.rule)
        self.icon_canvas.pack(side=tk.LEFT, padx=(0, 12))
        self.icon_canvas.create_text(40, 40, text="未选择", fill=self.muted, font=self.label_font)
        self.icon_canvas.bind("<Button-1>", lambda e: self.select_icon())

        icon_right = tk.Frame(icon_frame, bg=self.bg)
        icon_right.pack(side=tk.LEFT, fill=tk.Y, expand=True)

        self.btn_select_icon = tk.Button(icon_right, text="选择图标文件", font=self.build_font,
                                         bg=self.bg2, fg=self.ink, activebackground=self.bg3,
                                         activeforeground=self.ink, relief=tk.FLAT,
                                         cursor="hand2", command=self.select_icon)
        self.btn_select_icon.pack(anchor="w", pady=(8, 4))

        self.lbl_icon_info = tk.Label(icon_right, text="支持 PNG / JPG / WEBP 等格式\n图片会自动裁剪为正方形",
                                      font=self.label_font, bg=self.bg, fg=self.muted, justify=tk.LEFT)
        self.lbl_icon_info.pack(anchor="w")

        # 4. 构建
        self._make_section(frame, "4", "构建 APK", 9)

        # 构建类型
        variant_frame = tk.Frame(frame, bg=self.bg)
        variant_frame.grid(row=10, column=0, columnspan=2, sticky="w", pady=(4, 8))
        tk.Label(variant_frame, text="构建类型:", font=self.build_font, bg=self.bg, fg=self.ink).pack(side=tk.LEFT)
        self.variant_var = tk.StringVar(value="release")
        tk.Radiobutton(variant_frame, text="Release（推荐）", variable=self.variant_var, value="release",
                       bg=self.bg, fg=self.ink, selectcolor=self.bg3, activebackground=self.bg,
                       font=self.build_font).pack(side=tk.LEFT, padx=(8, 0))
        tk.Radiobutton(variant_frame, text="Debug", variable=self.variant_var, value="debug",
                       bg=self.bg, fg=self.ink, selectcolor=self.bg3, activebackground=self.bg,
                       font=self.build_font).pack(side=tk.LEFT, padx=(8, 0))

        # 构建按钮
        self.btn_build = tk.Button(frame, text="开始构建", font=("Microsoft YaHei UI", 11, "bold"),
                                   bg=self.accent, fg=self.bg, activebackground="#29b6f6",
                                   activeforeground=self.bg, relief=tk.FLAT, cursor="hand2",
                                   command=self.start_build)
        self.btn_build.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(8, 6))

        # 下载按钮（默认隐藏）
        self.btn_download = tk.Button(frame, text="下载 Game.apk", font=("Microsoft YaHei UI", 10, "bold"),
                                      bg=self.accent2, fg=self.bg, activebackground="#00c853",
                                      activeforeground=self.bg, relief=tk.FLAT, cursor="hand2",
                                      command=self.download_apk)
        self.btn_download.grid(row=12, column=0, columnspan=2, sticky="ew")
        self.btn_download.grid_remove()

        # 状态标签
        self.lbl_status = tk.Label(frame, text="等待操作", font=self.build_font,
                                   bg=self.bg2, fg=self.muted, padx=8, pady=6)
        self.lbl_status.grid(row=13, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        # 日志区域
        self._make_section(frame, "日志", "", 14)
        self.txt_log = scrolledtext.ScrolledText(frame, wrap=tk.WORD, font=("Consolas", 9),
                                                  bg=self.bg3, fg=self.muted, relief=tk.FLAT,
                                                  insertbackground=self.muted, height=14, state=tk.DISABLED)
        self.txt_log.grid(row=15, column=0, columnspan=2, sticky="nsew", pady=(4, 0))

        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(15, weight=1)

    def _make_section(self, parent, num, title, row):
        """创建带编号的分区标题"""
        lbl = tk.Label(parent, text=f"  {num}  {title}", font=("Microsoft YaHei UI", 10, "bold"),
                       bg=self.bg2, fg=self.accent, padx=8, pady=6)
        lbl.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 4))

    def load_defaults(self):
        """加载当前配置"""
        cfg = get_current_config()
        if cfg["app_id"]:
            self.entry_appid.insert(0, cfg["app_id"])
        if cfg["app_name"]:
            self.entry_appname.insert(0, cfg["app_name"])

        # 检查默认图标
        default_icon = os.path.join(WWW_DIR, "icon", "icon.png")
        if os.path.exists(default_icon):
            self.icon_path = default_icon
            self.update_icon_preview()
            self.lbl_icon_info.config(text=f"默认图标: www/icon/icon.png\n({os.path.getsize(default_icon)} bytes)")

    def select_icon(self):
        """选择图标文件"""
        path = filedialog.askopenfilename(
            title="选择图标文件",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"), ("所有文件", "*.*")]
        )
        if path:
            self.icon_path = path
            self.update_icon_preview()
            size = os.path.getsize(path)
            self.lbl_icon_info.config(text=f"已选择: {os.path.basename(path)}\n({size} bytes)")

    def update_icon_preview(self):
        """更新图标预览"""
        if not self.icon_path or not os.path.exists(self.icon_path):
            return
        try:
            img = Image.open(self.icon_path).convert("RGBA")
            w, h = img.size
            min_dim = min(w, h)
            left = (w - min_dim) // 2
            top = (h - min_dim) // 2
            square = img.crop((left, top, left + min_dim, top + min_dim))
            thumb = square.resize((76, 76), Image.LANCZOS)
            self.icon_photo = ImageTk.PhotoImage(thumb)
            self.icon_canvas.delete("all")
            self.icon_canvas.create_image(40, 40, image=self.icon_photo)
        except Exception as e:
            self.icon_canvas.delete("all")
            self.icon_canvas.create_text(40, 40, text="错误", fill=self.danger, font=self.label_font)

    def append_log(self, text):
        """追加日志到文本框"""
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, text)
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)

    def set_status(self, text, color=None):
        """设置状态栏"""
        self.lbl_status.config(text=text)
        if color:
            self.lbl_status.config(fg=color)

    def poll_log(self):
        """轮询日志队列更新 UI"""
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.append_log(msg)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_log)

    def start_build(self):
        """开始构建"""
        app_id = self.entry_appid.get().strip()
        app_name = self.entry_appname.get().strip()

        if not app_id or not app_name:
            messagebox.showwarning("提示", "请填写包名和 App 名称")
            return

        # 清空日志
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.delete(1.0, tk.END)
        self.txt_log.config(state=tk.DISABLED)
        self.btn_download.grid_remove()

        self.btn_build.config(state=tk.DISABLED, text="构建中...", bg=self.rule)
        self.set_status("正在构建，请稍候...", self.accent)

        variant = self.variant_var.get()

        def build_thread():
            try:
                log_to_queue(self.log_queue, "[提示] 第一次构建可能需要 5-15 分钟下载依赖，请耐心等待...")
                log_to_queue(self.log_queue, "[提示] 如果卡住超过 20 分钟，可能是 Windows Defender 干扰，建议临时关闭实时保护。")
                prepare_files(app_id, app_name, self.icon_path, self.log_queue)
                success = run_gradle_build(variant, self.log_queue)
                if success:
                    self.root.after(0, lambda: self.set_status("构建成功！", self.accent2))
                    self.root.after(0, lambda: self.btn_download.grid())
                else:
                    self.root.after(0, lambda: self.set_status("构建失败，请查看日志", self.danger))
            except Exception as e:
                log_to_queue(self.log_queue, f"构建异常: {e}")
                self.root.after(0, lambda: self.set_status("构建异常", self.danger))
            finally:
                self.root.after(0, lambda: self.btn_build.config(
                    state=tk.NORMAL, text="开始构建", bg=self.accent))

        threading.Thread(target=build_thread, daemon=True).start()

    def download_apk(self):
        """打开 APK 所在目录"""
        apk_path = os.path.join(WORKSPACE_DIR, "Game.apk")
        if os.path.exists(apk_path):
            os.startfile(WORKSPACE_DIR)
        else:
            messagebox.showerror("错误", "APK 文件不存在")

# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = BuilderApp(root)
    root.mainloop()
