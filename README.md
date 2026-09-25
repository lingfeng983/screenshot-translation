# 截图翻译工具 - 使用说明

> 📝 更新日志见 [CHANGELOG.md](CHANGELOG.md)

## 🚀 快速开始

### 首次安装
```bash
# 1. 克隆仓库
git clone https://gitee.com/lingfeng983/screenshot-translation.git
cd screenshot-translation

# 2. 安装依赖
pip install -r requirements.txt

# 3. 创建本地配置（含API密钥，不入库）
copy config.example.ini config.ini

# 4. 下载 OCR 组件（Tesseract 便携包+语言数据，约110MB，不入仓库，走 Release 附件）
python scripts/setup_ocr.py

# 5. 启动
python main.py
```

> **OCR 组件分发方式**：仓库只保留源码；Tesseract 便携包与语言数据以分卷 zip
> 附件形式发布在 Gitee [Releases](https://gitee.com/lingfeng983/screenshot-translation/releases)，
> `scripts/setup_ocr.py` 自动下载、SHA256 校验并解压。
> 离线环境可用 `python scripts/setup_ocr.py --local <分卷目录>`。
> 维护者更新组件后运行 `python scripts/make_release_package.py` 重新打包。

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

### 3. 截图翻译流程
1. 按快捷键或点击托盘菜单启动截图
2. 在屏幕上拖拽选择要翻译的区域（选区会自动磁吸对齐到附近窗口边缘与屏幕边，蓝色虚线指示吸附目标）
3. 程序自动进行OCR识别和翻译
4. 显示翻译结果对话框

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
- **测试翻译** - 保存前先验证当前API配置是否可用
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

### 快捷键设置
可在配置面板中自定义全局快捷键

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
**现象**: 按快捷键无反应
**可能原因**:
- 快捷键被其他程序占用
- 权限不足（需要管理员权限）
- keyboard库兼容性问题

**解决**:
- 尝试更换快捷键组合
- 以管理员身份运行程序
- 检查杀毒软件是否阻止

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
├── config.py            # 配置管理（[Translation]/[OCR]/[Overlay]/[Hotkey]/[Startup]）
├── config.ini           # 配置文件（旧版本自动迁移补齐新段）
├── CHANGELOG.md         # 更新日志
├── requirements.txt     # 依赖列表
├── run_fixed.bat        # 启动脚本
├── scripts/
│   ├── make_release_package.py  # OCR组件分卷打包（上传Release用）
│   └── setup_ocr.py             # 从Release下载恢复OCR组件
├── test_translation_providers.py  # 翻译架构回归测试
├── test_ocr_providers.py          # OCR架构回归测试
├── test_features.py               # 基础功能回归测试
├── managers/                # 功能模块
│   ├── provider_common.py   # ★ 插件架构公共基座（注册表/自动发现/基类/降级链）
│   ├── translation.py       # 翻译管理器（分发/缓存/降级链）
│   ├── ocr.py               # OCR管理器（分发/重试/降级）
│   ├── screenshot.py        # 截图遮罩（磁吸/气泡/角标记）与结果对话框
│   ├── styles.py            # 统一深色蓝QSS主题
│   ├── icon.py              # 程序化图标生成
│   ├── window_enum.py       # Win32可见窗口枚举（磁吸数据源）
│   ├── cache_manager.py     # 翻译缓存（持久化/LRU）
│   ├── retry_handler.py     # 指数退避重试
│   ├── thread_pool.py / qt_dispatcher.py / progress_manager.py
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

## 🆘 技术支持

如遇到问题，请检查：
1. 控制台输出的日志信息
2. 配置文件是否正确生成
3. 依赖包是否完整安装
4. 系统权限是否足够

程序会在控制台输出详细的运行日志，便于问题诊断。