# 更新日志 (CHANGELOG)

所有显著改动记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

本次为一次集中迭代：**翻译/OCR 全面插件化、免费翻译默认直连、结果框交互增强、界面整体美化、遮罩重制与磁吸**。

### 新增

#### 翻译
- **翻译 API 插件架构**：`managers/providers/` 插件目录 + 注册表 + 启动自动发现；新增翻译服务只需放一个定义 `register(registry)` 的模块，配置界面自动出现新选项
- **Edge 免费翻译（新默认 `edge_free`）**：Microsoft Edge 内置翻译同款端点，无需密钥，国内可直连（实测 HTTP 200）
- **谷歌免费翻译 `google_free`**：Copyfish 同款免费端点，无需密钥（国内裸连不可达，有代理时可用）
- **大模型(LLM)翻译 `llm`**：任意 OpenAI 兼容接口（OpenAI/DeepSeek/通义/Kimi/本地 Ollama），自动剥离推理模型的 `<think>` 段落
- **目标语言可配置**：不再硬编码译中文，支持中/繁/英/日/韩/法/德/西/俄
- **降级链**：主API(重试3次) → 配置的降级API(轻量重试2次) → Edge免费 → 谷歌免费，逐级兜底不中断
- **单次翻译指定语言**：`perform_translation` 支持 `force_refresh`（跳过缓存）与 `target_lang`（本次翻译专用语言）参数，缓存键按语言隔离
- **长文本自动分块**，百度/有道按各自接口上限切分

#### OCR
- **OCR 引擎插件架构**：`managers/ocr_providers/` 与翻译同构，扩展方式一致
- **Tesseract provider（默认）**：原预处理逻辑（放大/灰度/二值化/对比度）迁入，识别语言可配置（本地离线，`tesseract_langs` 配置项）
- **百度云OCR provider（`baidu_cloud`）**：在线识别，中文效果更佳，access_token 自动缓存刷新
- **模拟OCR provider（`mock`）**：兜底降级，引擎不可用时返回提示文本
- **OCR 降级链**：当前引擎(重试) → 本地Tesseract → 模拟OCR

#### 界面与交互
- **程序图标**：Pillow 程序化绘制（`managers/icon.py`，蓝色渐变圆角 + 对话气泡 + "译"字），托盘/窗口/任务栏统一，附多尺寸 ico
- **深色蓝主题**：统一 QSS（`managers/styles.py`）应用于配置窗口与结果对话框；遮罩暗角/磁吸参数由配置驱动
- **托盘交互**：左键单击直接截图、双击打开设置（防抖窗口跟随系统双击速度，夹取 0.30–0.50s）、右键菜单；启动时托盘通知使用方式
- **结果对话框**：复制译文/复制原文（2秒"已复制"反馈）、重新翻译（跳过缓存走降级链，后台执行）、目标语言下拉框（切换即重译，不改全局配置）
- **截图遮罩重制**：柔和暗角（去窗口级压暗，选区100%原屏亮度）、蓝色发光选区+四角标记、尺寸气泡（贴边自动翻转）、操作提示条
- **磁吸对齐**：选区四边吸附附近窗口边缘与屏幕边（阈值12px），蓝色虚线指示吸附目标，裁剪所见即所得；窗口枚举基于 Win32（`managers/window_enum.py`）
- **遮罩参数可调**：配置窗口"截图遮罩"分组，磁吸阈值（`snap_threshold`，0–48px）与暗角透明度（`dim_opacity`，0–180）滑条

### 修复
- **Enter 确认截图崩溃隐患**：`QKeyEvent` 误传 `mouseReleaseEvent` 导致访问 `event.pos()` 崩溃；确认逻辑统一收口到 `_confirm_selection()`
- **选区被压暗**：移除 `setWindowOpacity(0.6)`，暗角改由绘制层控制，挖空选区恢复原屏亮度
- **尺寸气泡出屏**：贴屏幕顶部/边缘选择时气泡自动翻转与夹取
- **防抖退出保护**：托盘 `cleanup()` 取消待触发的单击，避免退出瞬间误发截图
- **结果对话框迟到结果**：重译完成前关闭对话框不再报 `RuntimeError`

### 重构
- **`managers/provider_common.py`**：翻译与 OCR 两套插件体系统一到单一来源——通用注册表、自动发现、`BaseProvider`、`ProviderChainManager`、MD5签名；两包 `__init__` 改为薄封装（约 -300 行重复代码）
- 配置读取统一容错：非法值回退默认、越界夹取

### 变更
- 默认翻译 API：`libretranslate` → `edge_free`（国内可直连）
- 默认快捷键：`Ctrl+Shift+A` → `Alt+Z`（配置项 `alt+z`，按下直接截图翻译）
- 配置窗口更名"设置"，启动默认最小化到托盘（托盘通知提示用法）
- 旧配置文件自动迁移：自动补齐 `[OCR]`、`[Overlay]` 段
- **仓库只保留源码**：Tesseract 便携包+语言数据（约110MB）不入库；
  `scripts/make_release_package.py` 打为分卷 zip（≤90MB/卷）上传 Gitee Release 附件，
  克隆端 `scripts/setup_ocr.py` 自动下载、SHA256 校验、解压，支持 `--local` 离线恢复
- **分发包改为 onedir 目录形态**：`screenshot-translator.spec` + PyInstaller 打出
  `dist/ScreenshotTranslator/`（含图标、无控制台、内置Tesseract OCR与中英文语言包，
  开箱即用，整目录约298MB）；新增 `managers/app_paths.py` 统一双形态路径定位——
  打包态只读资源走 `_MEIPASS`（onedir 下即 `_internal`），可写的配置/缓存/日志
  落在 exe 同目录，首次启动自动生成默认 config.ini
- **OCR 瘦身打包**：`scripts/collect_ocr_bundle.py` 抽取运行必需的
  tesseract.exe+DLL+eng/chi_sim（排除约60MB训练工具），打包时自动收集最小集

### 性能与资源
- **启动 2.97s → 0.44s**：onedir 不再把 486MB 内容解压到 `%TEMP%\_MEI*` 后才运行，
  强制结束进程也不再遗留解包目录（此前每次异常退出留一份）
- **依赖去重裁剪**：tesseract 作为二进制数据收集时被依赖分析重复落到根目录，
  按真实 PE 导入表核实后仅保留 `libffi-8.dll`、`zlib1.dll`，删掉 47 个重复 DLL；
  同时剔除 Qt 的 QML/场景图/Vulkan/翻译包与 PIL 的 `_avif/_webp/_imagingtk` 等
  用不到的模块（binaries 212→158、datas 110→35）
- **OCR 引擎实例复用**：Tesseract provider 构造要探测引擎路径、跑一次
  `tesseract --version` 并扫盘定位 tessdata，改为按 provider 类缓存实例，
  配置重载时作废，一次翻译少起一个进程
- **结果态首帧 388ms → 100ms，重绘 2ms**：取色与排版（字体度量身踩）按
  「图像+块矩形+模式」缓存，换选区即作废；启动空闲期预热中文字体，
  避免第一次往画布画中文时现加载字体
- **截图大对象即时释放**：确认选区后 `crop().copy()` 并放下整屏原图（1440p 约 11MB），
  结果底图按需从 PIL 转换，不再常驻一份 1:1 QPixmap
- **日志轮转**：`screenshot_translation.log` 按 2MB × 3 份滚动，常驻托盘不再无限增长
- **测试与真实缓存隔离**：`test_features`/`test_config_ui` 走临时目录缓存文件，
  不再把结果写进项目根的 `translation_cache.json`

## [历史版本]

初始版本：截图 → Tesseract OCR → 翻译（LibreTranslate/必应/百度/有道）基础流程，含缓存、重试、进度显示、托盘与全局热键。
