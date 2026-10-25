# 截图翻译工具 - 使用说明

> 📝 更新日志见 [CHANGELOG.md](CHANGELOG.md)

## 🚀 快速开始

### 方式一：安装程序（推荐普通用户）
从 [Releases](https://github.com/lingfeng983/screenshot-translation/releases) 下载
`ScreenshotTranslator-Setup.exe`（约 30MB），双击安装：

1. 安装时会问你是否**下载 OCR 离线识别组件**（默认勾选，约 85MB，从同一 Release 的
   附件取，装到 `%USERPROFILE%\.screenshot-translation\resources\`）。
   断网/下载失败会让安装中止，重试或取消勾选即可只装程序本体
2. 装完托盘出现图标（若被折叠，点任务栏 `^` 展开并右键图标→设置常显）
3. 按 `Alt+Z` 截图翻译

> 不装 OCR 组件时，识别需改用设置里的在线 OCR（百度云等）或系统已装的 Tesseract。
> 使用百度云OCR/LLM 等需密钥的服务时，右键托盘→设置 填入密钥。

### 方式二：源码运行（开发者）

#### 首次安装
```bash
# 1. 克隆仓库
git clone https://github.com/lingfeng983/screenshot-translation.git
cd screenshot-translation

# 2. 安装依赖
pip install -r requirements.txt

# 3. 创建本地配置（含API密钥，不入库）
copy config.example.ini config.ini

# 4. 下载 OCR 组件（Tesseract 运行最小集，约85MB，不入仓库，走 Release 附件）
python scripts/setup_ocr.py               # 默认取最新 Release；固定版本用 --release v1.5.0

# 5. 启动
python main.py
```

> **OCR 组件分发方式**：仓库只保留源码；Tesseract 运行最小集（约 85MB）以
> `ocr-components.zip.00N` 附件形式发布在 GitHub [Releases](https://github.com/lingfeng983/screenshot-translation/releases)。
> 源码端 `python scripts/setup_ocr.py` 下载、SHA256 校验并解压到 `resources/`——
> 不带参数取最新 Release（`latest/download` 跳转），需要固定版本给 `--release <标签>`；
> 离线环境可用 `python scripts/setup_ocr.py --local <分卷目录>`。
> 安装程序走的是同一批附件，由 Setup 自己下载校验并解压到用户数据目录，
> 全程不起外部进程（所以不会弹控制台窗口）。
> 维护者更新组件后运行 `python scripts/collect_ocr_bundle.py` +
> `python scripts/make_release_package.py` 重新打包（包名固定，勿加日期）。

### 启动程序
```bash
# 方法1: 使用批处理脚本（推荐）
run_fixed.bat

# 方法2: 直接运行Python
python main.py
```

## 🎯 主要功能

### 1. 系统托盘操作
程序启动后在系统托盘显示图标，交互方式：
- **左键单击图标** - 直接开始截图翻译
- **双击图标** - 打开设置
- **右键图标** - 菜单（开始截图 / 设置 / 退出）

### 2. 快捷键操作
- **默认快捷键**: `Alt+Z`（配置项 `alt+z`）
- 按下快捷键即可直接启动截图翻译功能（可在设置面板中自定义）
- 注册走 **Windows 原生 `RegisterHotKey`**（系统直接投递按键，不受键盘钩子
  超时/被摘的影响）；该组合已被其他程序注册走时自动退回键盘钩子，
  并**弹托盘通知 + 写日志 + 在设置窗口提示**你换一个组合；占用是暂时的，
  对方程序退出后会自动切回原生注册（每 15s 重试一次）

### 3. 截图翻译流程
1. 按快捷键或点击托盘菜单启动截图
2. 按下的瞬间整屏就被**冻结成一张快照**（视频、时钟、进度条都停在那一刻，框选和看译文时都不会再动，框到的画面与最终识别的就是同一帧）；
   **多显示器时整张虚拟桌面一起冻结、一起覆盖**，副屏上的内容也能直接框选
3. 在屏幕上拖拽选择要翻译的区域（选区会自动磁吸对齐到附近窗口边缘与屏幕边，蓝色虚线指示吸附目标）
4. 程序自动进行OCR识别和翻译，遮罩保持不关闭，选区内显示"识别并翻译中…"
5. 译文就地覆盖回原文位置（取原文周围的背景色填底、按块高自动缩字），遮罩不关闭
6. 选区下方浮动工具条可切换 **原文 / 双语 / 译文** 三态、换目标语言重译、复制、钉住、保存、关闭
7. 鼠标悬停或单击选区内的段落，会在下方气泡卡片里显示该段原文与译文（Ctrl+C 只复制该段）
8. 行级坐标不可用时（OCR 引擎不支持或译文行数对不齐）自动降级为整段气泡卡片展示

多显示器说明：各屏缩放比例一致时覆盖全部显示器（副屏在主屏左侧/上方也行）；
缩放比例不一致（混合 DPI）时只覆盖主屏，因为单一缩放系数无法把坐标换算准确。

遮罩内快捷键：`ESC` 退出、`Enter` 确认选区、`T`/`1`/`2`/`3` 切换展示模式、`Ctrl+C` 复制、
`Ctrl+S` 保存结果图、`P` 钉屏、`R` 失败后重试；结果态下再按一次截图热键等同于取消。
钉住的窗口支持拖动、滚轮缩放、双击关闭、`Ctrl+C` 复制图片。

## ⚙️ 配置说明

### 翻译API设置
支持多种翻译服务（在配置窗口的下拉框中选择，并可为每个API填入对应密钥）：
- **Edge翻译(免费)** (默认) - Microsoft Edge 内置翻译同款免费接口，微软翻译服务国内可直连，无需任何密钥
- **谷歌翻译(免费)** - Copyfish 扩展同款免费接口，无需密钥，但国内裸连不可达（有代理时可用）
- **LibreTranslate** - 免费开源翻译，可自建服务，可自定义API地址
- **必应翻译** - 需要 API 密钥
- **百度翻译** - 需要 应用ID + API密钥
- **有道翻译** - 需要 应用ID + API密钥
- **大模型(LLM)** - 任意 OpenAI 兼容接口（OpenAI / DeepSeek / 通义千问 / Kimi / 本地 Ollama 等），填入 API地址、密钥与模型名即可；本地 Ollama 可不填密钥（地址如 `http://localhost:11434/v1`）

还可配置：
- **目标语言** - 中文/繁体/英/日/韩/法/德/西/俄
- **降级API** - 主API失败后自动切换的备用API；整条降级链的最后会依次兜底尝试 Edge 免费翻译、谷歌免费翻译
- **测试翻译** - 保存前先验证当前API配置是否可用（后台线程执行，不会卡住设置窗口）
- **界面主题** - 深色（默认）/ 浅色（白色）两套主题，切换立即预览，保存后长期生效
- **截图遮罩** - 磁吸阈值（`snap_threshold`，0~48px，选区吸附窗口边缘的距离）与暗角透明度（`dim_opacity`，0~180），滑条调节，下次截图生效

### OCR引擎设置
支持多种识别引擎（配置窗口中可切换）：
- **Tesseract(本地)** (默认) - 本地离线识别，免费，需安装Tesseract（项目已内置便携版探测逻辑）
- **百度云OCR(在线)** - 中文识别效果更佳，每月有免费额度，需注册获取API Key
- **模拟OCR(降级)** - 引擎不可用时的兜底，返回提示文本

扩展新的OCR引擎与翻译API方式相同：在 `managers/ocr_providers/` 下新建定义了 `register(registry)` 的模块即可。

### 扩展新的翻译API（开发者）
翻译模块基于 provider 插件架构，新增翻译服务只需两步：

1. 在 `managers/providers/` 下新建一个 Python 文件（如 `myapi.py`）；
2. 定义 provider 类并实现 `register(registry)` 函数：

```python
from .base import TranslationProvider

class MyApiProvider(TranslationProvider):
    name = 'my_api'                    # 唯一标识（写入config.ini）
    display_name = '我的翻译'          # 配置界面显示名
    config_fields = (                  # 配置界面动态生成的输入框
        ('my_api_key', 'API密钥', True),   # (字段名, 显示名, 是否密码框)
    )

    def translate(self, text, source='auto', target='zh'):
        ...  # 返回译文；失败时抛异常即可，上层会自动重试/降级

def register(registry):
    registry.register(MyApiProvider)
```

程序启动时自动发现并注册，配置窗口会自动出现"我的翻译"选项及其字段，无需修改任何其他代码。

### 界面主题
设置窗口顶部“外观”分组可选择主题：

| 主题 | 底色 | 说明 |
|---|---|---|
| `dark` 深色（默认） | 深蓝灰 | 原视觉风格，深夜/暗色环境不刺眼 |
| `light` 浅色（白色） | 浅灰白 | 白色面板，白天或投影场景更清晰 |

切换后**立即预览**（整个应用即时换肤），点“保存”后写入配置长期生效；
配置项为 `[UI] theme`，旧配置文件启动时自动补齐。截图遮罩、结果卡片与
遮罩工具条是浮在屏幕内容之上的深色元素，风格独立，不随主题变化（白底卡片在亮屏幕上会看不清）。

### 快捷键设置
可在配置面板中自定义全局快捷键。快捷键分组里有一行实时状态：注册成功会写明走
系统投递还是键盘钩子；被别的程序占用时直接标红提示"原生注册失败，已退回键盘钩子"，
并按日志里的原因提示如何释放占用。开窗、改组合、点保存都会刷新这一行。

### 单实例
同一时间只允许运行一个实例（命名互斥体 `Local\ScreenshotTranslator.SingleInstance`）。
重复启动只会弹一句"已经在运行中，请用托盘图标或 Alt+Z"然后退出，避免出现两套托盘
图标、两个全局热键互相抢占、后启动的进程覆盖前一个写好的缓存。开发/联调确实要多开时：

```bash
set ST_ALLOW_MULTI_INSTANCE=1 && python main.py
```

### 启动行为
- 启动后最小化到托盘（默认）
- 或启动时显示配置窗口

## 🔧 故障排除

### 常见问题及解决方案

#### 1. OCR功能不可用
**现象**: 显示"使用模拟OCR识别"
**原因**: pytesseract库或Tesseract OCR未正确安装
**解决**: 
- 安装Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki
- 确保tesseract.exe在系统PATH中

#### 2. 快捷键不生效
**现象**: 按快捷键无反应或过一段时间才失效

先看日志（源码运行在项目根，打包运行在 `%USERPROFILE%\.screenshot-translation\`）：
- `快捷键已用系统原生接口注册` — 走的是系统 `RegisterHotKey`，最稳；
- `快捷键 xxx 已被其它程序占用` + `已用键盘钩子注册` — 该组合被别的程序抢了，
  程序退到键盘钩子模式（能收到按键，但在提权窗口获得焦点、锁屏/远程桌面切换
  之后可能收不到）。**换一个没被占用的组合**（例如 `ctrl+alt+z`）即可恢复原生注册。

**可能原因**:
- 快捷键被其他程序占用（截图/剪贴板工具最常见）
- 提权窗口（UAC 提示、以管理员运行的程序）获得焦点时，非提权程序收不到按键
- 非键盘布局对应的键名写错，注册会直接失败并在日志里写明

**解决**:
- 换一个组合（先试 `ctrl+alt+z`，该组合一般无人占用）：程序启动时如果发现
  组合被占用，会直接弹一条托盘通知告诉你当前是兼容模式，不用自己翻日志
- 占用只是暂时的：抢走组合的程序退出后，程序会自动切回原生注册（不必重启）
- 确认被杀毒软件拦截，必要时以管理员身份运行

#### 3. 托盘菜单无响应
**现象**: 右键托盘图标无反应
**解决**:
- 重启程序
- 检查系统托盘是否正常工作
- 查看程序日志输出

#### 4. 程序退出异常
**现象**: 退出时报错或无法完全关闭
**解决**:
- 使用托盘菜单的"退出"选项
- 避免强制终止进程
- 检查是否有后台线程仍在运行

## 📋 技术规格

### 运行环境要求
- **操作系统**: Windows 10/11
- **Python版本**: 3.7+
- **必需依赖**: PyQt5, Pillow, pyautogui, requests, pystray, keyboard

### 目录结构
```
ScreenshotTranslation/
├── main.py              # 主程序入口（含设置窗口）
├── config.py            # 配置管理（[Translation]/[OCR]/[Overlay]/[UI]/[Hotkey]/[Startup]）
├── config.ini           # 配置文件（旧版本自动迁移补齐新段）
├── CHANGELOG.md         # 更新日志
├── requirements.txt     # 依赖列表（跑程序）
├── requirements-dev.txt # 开发/发版工具（pytest、PyInstaller）
├── run_fixed.bat        # 启动脚本
├── screenshot-translator.spec  # PyInstaller onedir 打包配置（含依赖裁剪）
├── scripts/
│   ├── publish_release.py       # 一条命令出包＋发版（测试→构建→Setup→发 Release）
│   ├── setup.iss                # Inno Setup 7 安装脚本（OCR 组件由 Setup 内置下载）
│   ├── collect_ocr_bundle.py    # 抽出 Tesseract 运行最小集 → dist/ocr-bundle
│   ├── make_release_package.py  # OCR组件分卷打包（上传Release用，并写 ocr-artifact.iss）
│   └── setup_ocr.py             # 从Release下载恢复OCR组件（源码态/手工补装）
├── tests/                   # pytest 回归测试（从项目根直接 python -m pytest tests/）
│   ├── conftest.py                  # 共用装置：sys.path、会话级 QApplication、online/smoke 标记
│   ├── _runner.py                   # 脚本运行器的用例发现（python tests/test_xxx.py 时走它）
│   ├── test_translation_providers.py  # 翻译架构测试
│   ├── test_ocr_providers.py          # OCR架构测试
│   ├── test_features.py               # 基础功能测试
│   ├── test_config_ui.py              # 配置界面 + 遮罩结果态测试
│   └── test_single_instance.py        # 单实例互斥（仅 Windows）
├── managers/                # 核心功能模块
│   ├── provider_common.py   # ★ 插件架构公共基座（注册表/自动发现/基类/降级链）
│   ├── app_paths.py         # ★ 路径定位（源码→项目根 / 打包→~/.screenshot-translation，含旧数据迁移）
│   ├── single_instance.py   # ★ 命名互斥体单实例守卫（重复启动时提示并退出）
│   ├── translation.py       # 翻译管理器（分发/缓存/降级链）
│   ├── ocr.py               # OCR管理器（分发/重试/降级）
│   ├── screenshot.py        # 截图遮罩（磁吸/气泡/角标记）+ 就地译文结果态
│   ├── result_render.py     # ★ 结果渲染（行块贴图覆盖/气泡卡片/裁剪合成）
│   ├── text_block.py        # ★ OCR行块模型 + 段落聚合（悬停/选块依据）
│   ├── overlay_toolbar.py   # ★ 选区浮动工具条（三态/语言/复制/钉住/保存）
│   ├── pin_window.py        # ★ 结果钉屏浮窗（可多开、置顶）
│   ├── styles.py            # ★ 双主题QSS（深色/浅色，调色板驱动，支持实时预览）
│   ├── icon.py              # 程序化图标生成
│   ├── window_enum.py       # Win32可见窗口枚举（磁吸数据源）
│   ├── cache_manager.py     # 翻译缓存（持久化/LRU）
│   ├── retry_handler.py     # 指数退避重试
│   ├── thread_pool.py / qt_dispatcher.py
│   ├── hotkey.py / tray.py / error_handler.py
│   ├── providers/           # 翻译API插件目录（扩展放这里）
│   │   ├── base.py          #   翻译基类+目标语言表（继承公共基座）
│   │   ├── edge_free.py     #   Edge免费翻译（微软翻译，国内可直连，默认）
│   │   ├── google_free.py   #   谷歌免费翻译（Copyfish同款）
│   │   ├── libretranslate.py / bing.py / baidu.py / youdao.py
│   │   └── llm.py           #   大模型翻译（OpenAI兼容）
│   └── ocr_providers/       # OCR引擎插件目录（扩展放这里）
│       ├── base.py          #   OCR基类（继承公共基座）
│       ├── tesseract.py     #   本地Tesseract（默认，含图像预处理）
│       ├── baidu_cloud.py   #   百度云OCR（在线，云引擎扩展示例）
│       └── mock.py          #   模拟OCR（兜底降级）
└── resources/
    ├── icon.png / icon.ico  # 程序图标（构建时自动生成）
    └── tessdata/            # OCR语言数据
```

### 插件架构一览
```
配置窗口 ──选择──▶ 管理器(ProviderChainManager) ──按名分发──▶ Provider
                     │ 重试/降级链/失败文本转换                │
                     ▼                                        ▼
             provider_registry                       translate() / recognize()
             (managers/provider_common.py)          失败抛异常→上层重试→降级链
```
新翻译API放 `managers/providers/`，新OCR引擎放 `managers/ocr_providers/`，
定义 `name`/`display_name`/`config_fields` + 工作方法 + `register(registry)` 即自动上架配置界面。

### 打包（onedir + 安装包）
```powershell
# 1. 打包程序本体（不含 OCR，整个目录约 73MB）
#    ST_VERSION 会被写进 exe 的 Windows 版本资源（右键属性看得到）；不给就没有版本信息
#    环境变量按你的 shell 给：PowerShell 用 $env:，cmd 用 set，Bash 才用 VAR=值 前缀
$env:ST_VERSION = "1.3.0"      # cmd: set ST_VERSION=1.3.0
python -m PyInstaller screenshot-translator.spec --noconfirm

# 2. 出 OCR 组件附件（安装时要从 GitHub Release 拉它）
python scripts/collect_ocr_bundle.py      # → dist/ocr-bundle（约225MB）
python scripts/make_release_package.py    # → dist/release/ocr-components.zip.001 + .sha256（约85MB）
                                          #   同时写 ocr-artifact.iss：安装脚本编译期要的
                                          #   附件名/字节数/SHA256，所以这一步必须在第 3 步之前

# 3. 编译安装器（先把 dist/ScreenshotTranslator/ 拷进 release/onedir/）
iscc.exe /DMyAppVersion=1.3.0 /DMyAppOcrTag=v1.3.0 scripts\setup.iss
# 产物：release/ScreenshotTranslator-Setup.exe（约30MB，唯一对外分发包）
# 输出里的 `OCRDownloadReady=1 part=...` 表示内置下载真编进去了；
# 打成 `OCRDownloadReady=0 reason=...` 时安装包同名但勾了 OCR 就会中止，
# publish_release.py 抓这行，未就绪直接中止发版
```
> 上面三步 `python scripts/publish_release.py --version 1.3.0` 一条命令串完，
> 顺带提交、打 tag、发 Release。编译安装脚本需要 Inno Setup 7（内置下载是 7 的能力）。

Tesseract **不再打进 exe**：安装程序在装填阶段由 Setup 自己从 GitHub Release 附件下载
`ocr-components.zip.001`，SHA256 由 Setup 校验、解压到
`%USERPROFILE%\.screenshot-translation\resources\`，进度就在向导里，不起外部进程所以
不会弹控制台窗口。同一标签已装过则跳过下载（靠 `ocr-version.txt` 标记）。
**卸载不会删除**这批组件（它们装在用户目录、由所有版本共用；卸载时删掉会让每次
覆盖升级都重新下载 85MB），需要清理时手工删除 `%USERPROFILE%\.screenshot-translation\resources\`。
代价是安装时要联网，好处是安装包从 105MB 降到 30MB，225MB 的二进制不跟着每个包跑。

打包时自动裁剪无用依赖：Qt 的 QML/场景图/Vulkan 插件与翻译文件、PIL 的
`_avif/_webp/_imagingtk` 等扩展。

exe 启动即加载本目录下的 `_internal`，不再解包到临时目录。可写文件统一落在
`%USERPROFILE%\.screenshot-translation\`（`config.ini`、`translation_cache.json`、
按 2MB × 3 轮转的 `screenshot_translation.log`）——其中 `config.ini` 只在
你**改过设置之后**才生成，默认配置全程在内存里用，不会凭空丢一个文件出来。
产物目录保持只读——放进
Program Files 或整目录覆盖升级都不会丢配置；旧版本留在 exe 同目录的配置与缓存
首次启动会自动搬进该目录（只补缺，不覆盖已有文件）。源码运行仍在项目根。

### 测试
```powershell
# Windows 上先把输出编码设好（$env:PYTHONIOENCODING="utf-8"），否则中文用例会乱码
$env:PYTHONIOENCODING = "utf-8"
python -m pytest tests/ -q -rs                 # 全程离线，发版前必跑
$env:ST_RUN_ONLINE = "1"; python -m pytest tests/ -q -m ""   # 追加真连各翻译接口
$env:ST_RUN_SMOKE = "1"; python -m pytest tests/ -q -m ""    # 追加真拉起 main.py（会注册全局热键）
```
不跑 pytest 也能直接 `python tests/test_features.py`：用例由 `tests/_runner.py`
按模块命名空间发现（不再手抄名单），需要 pytest fixture 的那几条会列出名字而不是
静默跳过。开发/发版工具装在 `requirements-dev.txt`（pytest、PyInstaller）。
界面用例跑在临时数据目录里：配置、缓存、日志、OCR 路径都被重定向到 tmp，
不碰项目根的 `config.ini`；模态弹窗换成记录型假弹窗，所以保存流程会真的走一遍。
遮罩断言用真实平台的 `grab()` 取像素（offscreen 平台没有中文字体，绘制会静默失败），
滚轮用例带一个未加过滤的对照组，证明事件本身有效而不是空跑。
冒烟用例会带着 `ST_ALLOW_MULTI_INSTANCE=1` 拉起 `main.py`（否则第二次启动会被
单实例守卫挡掉），单实例本身的用例只在 Windows 跑，并且各自用唯一的互斥体名。

## 🆘 技术支持

如遇到问题，请检查：
1. 控制台输出的日志信息
2. 配置文件是否正确生成
3. 依赖包是否完整安装
4. 系统权限是否足够

程序会在控制台输出详细的运行日志，便于问题诊断。