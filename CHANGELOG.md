# 更新日志 (CHANGELOG)

所有显著改动记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### 修复
- **改一次设置就崩溃**：配置文件里缺 `[Translation]`/`[Hotkey]`/`[Startup]`
  任一段时（老文件或手改过的文件都很常见），点“保存”会让对应 setter 抛
  `NoSectionError`。现在启动时六段逐个补齐，setter 也自带缺段补建
- **快捷键升级后彻底失灵**：看门狗线程里直接调 `RegisterHotKey(None, …)`
  会把 WM_HOTKEY 投给不泵消息的看门狗线程，注册“成功”却永远收不到按键，
  同时兜底的键盘钩子已被摘掉。升级动作改回 Qt 主线程执行
- `Startup.minimize_to_tray` 写成非法布尔值时不再抛 `ValueError`（回退默认
  `true`），配置落盘的临时文件改为进程+线程唯一命名，权限收紧移到替换之前
- **OCR 子进程无上限挂死**：pytesseract 的 `timeout` 参数此前没传，引擎卡在
  训练数据/异常图像上会让整条识别链一直转。现在每次识别有 60 秒硬上限，超时
  后子进程被杀掉并抛 `ProviderTimeoutError`（不可重试，直接降级到下一引擎）
- **引擎缺失时报“未识别到文本”**：Tesseract 可执行文件或训练数据不在位时，
  错误信息现在明确指向「用安装包重新安装并勾选下载 OCR 离线识别组件」并给出
  组件目录，同时按配置的语种逐个校验 `.traineddata` 是否齐备
- **exe 与 tessdata 版本错配**：可执行文件和训练数据此前各自独立探测，可能
  一个是 `resources\` 里下载的、另一个是系统安装的；现在以选中的 exe 为准，
  优先用它同级的 tessdata
- **密钥泄漏到日志**：百度 OCR 的 `access_token`、`api_key`、`secret_key`
  原先拼在 URL query 里，异常文本和 requests 日志会把它写进日志文件。凭证改为
  表单字段提交，并在根 logger 上加过滤器，凡是日志里的带 query URL 一律截断
  成 `https://host/path?…`；翻译侧的签名 URL 同样受益
- **百度错误码分类**：令牌失效（2160xx）自动刷新重取一次；限流/额度类
  （17/18/2163xx）保留原始文本；图像超尺寸（4MB、长边 4096、短边 15）先在本地
  等比降采样再上传，并把返回坐标按同一比例还原回原图
- **provider 构造与令牌刷新的竞态**：多线程（多选区并发翻译）同时首次构造
  provider 或同时刷新 access_token 会重复初始化/重复请求令牌，现在分别加锁
- **“空区域”白跑两轮**：识别为空时的短路判定原先只看当前配置的引擎，
  云端配置 + 本地实际尝试的组合会误判为可重试，把整条降级链再跑一遍。现在按
  真正跑过的那次结果定案
- `_read_config` 不再静默吞掉配置读取异常（`MissingSectionHeaderError` 等会
  留下日志），翻译/OCR 的失败文案统一收敛为限定长度的真实异常摘要
- **翻译最坏等上几分钟**：整条降级链现在共用一个时限（默认 25 秒，截图流程
  给 40 秒），到点直接返回失败文本并跳过剩余引擎。单次请求的超时也从
  “一个 30 秒数字”改成 (连接 5 秒, 读取 10 秒) 并按剩余预算夹取——requests 的
  超时算的是每次 socket 操作，慢接口靠不断续命地读能把一级降级拖到分钟级，
  预算只剩 2 秒时不会再给 10 秒读超时
- **接口明确拒绝还在重试**：HTTP 4xx（408/425/429 与 5xx 除外）以及百度/有道
  的鉴权、额度、参数类错误码统一归为不可重试的 `ProviderRejectedError`，
  立刻降级到下一个引擎；只有 5xx、限流与传输层失败算 `ProviderUnavailableError`
  走退避。密钥填错不再把整条链的退避时间等完
- **关掉遮罩还在烧额度**：每次“选区 → 识别 → 翻译”都带一个取消令牌（取消信号
  + 整体时限），重新框选、按 R 重试、切换语言都会让在途请求收手，遮罩关闭时
  立即中止；退避等待改用 `Event.wait` 并可被时限截断，不再睡满整段
- **缓存记错服务方**：译文此前记在“配置的引擎”名下，主引擎坏掉期间兜底引擎的
  译文会顶着主引擎的名字被长期复用。现在按真正出译文的那个引擎记账，读取沿
  降级链的引擎名依次查（否则主引擎故障期同一句话每次 miss、每次重跑整条链）；
  空串译文也算命中，不再每帧重问一次
- **provider 把异常类型抹平**：`except Exception: raise Exception(...)` 会丢掉
  重试判定所需的具体类型（Edge 免费接口最明显）。现在只归一传输层异常，其余
  原样上抛；响应不是合法 JSON（网关返回 HTML）单独按可重试处理
- **签名凭证进了 URL**：百度/有道翻译原先用 GET 把 `appid`/`appKey`、`salt`、
  `sign` 全拼在 query 上，异常文本与各级代理的访问日志都会带上。改为 POST
  表单体提交
- **LLM 翻译长文本**：整段一次请求容易顶到上下文上限、且失败即全失败，现在按
  2000 字分块逐次请求（读超时放宽到 30 秒）；待译文本用 `<text_to_translate>`
  定界，system 提示明确这段是数据不是指令，定界符本身出现在原文里时替换成同形
  字符，避免截图内容把模型指令冲掉。LibreTranslate 同样补上此前缺失的分块
- 退出清理里释放翻译链路复用的连接池（`translation_manager.close()`）
- **点退出要等近一分半**：清理做完后进程迟迟不走，根因是解释器收尾阶段会
  无条件 join 线程池的工作线程（`concurrent.futures` 自 3.9 起把它们设成
  非 daemon 并注册退出等待），而 worker 可能正卡在一次网络读上，实测最久约
  90 秒。现在清理阶段最多等在飞任务 1.5 秒，随后把翻译缓存与日志落盘、
  刷新 handler，再直接结束进程
- **关闭线程池会卡住提交**：`shutdown()` 原先持着提交锁等 worker 结束，这期间
  任何 `submit()` 都被一起卡住。现在锁内只摘执行器引用，等待挪到锁外，并新增
  按超时收尾的 `wait_pending()`（不再靠私有属性数线程）
- **托盘点“退出”没反应**：菜单回调跑在 pystray 自己的线程里，在那里直接调
  `QApplication.quit()` 只是给主线程置个标志，事件循环仍在睡，要等下一个事件
  才被带走。现在把退出动作投递回 Qt 主线程执行
- **遮罩在场时弹错误框**：截图遮罩是全屏置顶窗口，其上再弹模态框会把用户卡在
  “遮罩关不掉、框也看不见”的位置（离线测试里表现为整条流程直接挂死）。遮罩
  显示期间错误只记日志、由遮罩内的结果区展示，遮罩释放后恢复弹窗；退出清理
  期间永久禁框，避免迟到的错误框挡住退出
- 截图流程、线程池与清理失败路径改用 `logger.exception`：打包后没有 stderr，
  只打到控制台的堆栈等于没打

## [v1.3.0] — 2026-10-10 — 安装包瘦身：OCR 组件改为安装时从 Gitee 下载

### 变更
- **交付物只剩 `ScreenshotTranslator-Setup.exe`**：不再产出单文件 exe，
  onedir 仅作为安装器的输入目录（`release/onedir/`）
- **Tesseract 不再打进 exe**：225MB 的 OCR 运行最小集改为装填阶段从本仓库
  Gitee Release 附件下载（`ocr-components.zip.00N` + `.sha256`），解压到
  `%USERPROFILE%\.screenshot-translation\resources\`；安装包因此从 105MB 降到 30MB。
  新增 `scripts/fetch_ocr.ps1`（分卷合并、SHA256 校验、按 `ocr-version.txt`
  标记跳过重复下载、先解到临时目录再整体替换），
  安装脚本 `scripts/setup.iss` 在 `PrepareToInstall` 里调它
- **下载失败即中止安装**：`PrepareToInstall` 返回错误串让 Inno 在任何文件落盘前
  退出；勾选框「下载 OCR 组件」可取消，用于确实不需要本地识别的场景
- **卸载会清掉下载的识别组件**（`resources\tesseract`、`resources\tessdata` 与
  标记文件），用户数据目录里的配置与缓存保留
- **OCR 组件附件包名去掉日期**（固定 `ocr-components`）：装填器按名字取附件，
  带日期会让升级后的安装包找不到文件；分卷改由 `dist/ocr-bundle` 最小集产出
  （85MB，单个附件即可，不必再分卷）

### 修复
- **打包脚本漏了 OCR 出包步骤**：`scripts/publish_release.py` 现在一并产出
  OCR 附件分卷，并把版本号与 Release 标签一起注入 iss
  （`/DMyAppVersion` `/DMyAppOcrTag`），避免安装包引用的 tag 与实际上传附件的 tag 脱节
- **`setup.iss` 缺 UTF-8 BOM**：中文向导文案会被 Inno 按系统 ANSI（GBK）解释成乱码，
  现在文件带 BOM

### 测试
- 新增 `test_app_paths_ocr_component_location`：钉住「安装落盘位置」与
  「运行时探测首位候选」是同一个目录，防止两侧各改一半导致装完没有本地识别
- `scripts/fetch_ocr.ps1` 按本地 HTTP 源全量跑过：下载→校验→解压→识别出文字；
  同一标签重跑走跳过分支；标签不存在时退出码为 1
- pytest 48 passed

## [v1.2.0] — 2026-10-10 — 遮罩冻结、快捷键可靠性、默认配置不落盘与翻译提速

### 修复
- **截图时界面锁不住（选区里的内容还在刷新）**：遮罩是
  `WA_TranslucentBackground` 窗口，选区靠 `CompositionMode_Clear`“挖空”
  露出实时桌面，于是框选和看译文时自刷新的内容（视频、时钟、进度条）会继续
  动，框到的画面与最终识别到的画面还可能不是同一帧。现在按快捷键抓到的那一帧
  会铺在遮罩底层，选区从同一张冻结图重绘（选区外仍压暗角），结果态与加载态
  同样走冻结图：整个遮罩生命周期内屏幕是静止的一张快照
  （`managers/screenshot.py`，回归测试 `test_screenshot_overlay_freezes_screen`）。
  冻结图在遮罩关闭时释放（整屏位图是遮罩里最大的一块内存）
- **快捷键“时灵时不灵”：落到键盘钩子后就再也回不来了**：原生注册失败最常见的
  原因是组合被别的程序临时占着（本机实测默认的 `alt+z` 就是这种情况），而钩子
  会被系统在回调超时后静默摘掉、提权窗口/锁屏切换期间收不到按键。现在看门狗
  每次健康检查都顺手重试一次原生注册，占用一解除就自动切回系统投递，并摘掉
  钩子（避免同一次按键被两条路径各触发一次）；重试失败时保持钩子不动，不会让
  用户中途失去快捷键（`managers/hotkey.py`，回归测试
  `test_hotkey_backend_fallback_and_watchdog`）
- **兼容模式只写在日志里，用户看不见**：程序退到键盘钩子时启动后会用托盘通知
  明确告知“快捷键已降级为兼容模式”并建议换组合，不再等用户自己发现
  （`main.py::_notify_hotkey_conflict`）
- **防抖窗口会把刻意的第二次按键一起吃棹**：长按连发的防抖窗口 0.3s -> 0.15s
  （Windows 连发间隔约 30ms，0.15s 足够吞掉机器速率的连发，又不会把“关掉遮罩
  再按一次”的第二次按键吞掉；后者在用户看来就是快捷键没反应）
- **快捷键经常失效**：全局快捷键原来只有 keyboard 库的键盘钩子一条路，而钩子
  会被系统在回调超时（`LowLevelHooksTimeout`，默认 300ms）时**静默摘掉**，
  锁屏/远程桌面切换、提权窗口获得焦点后也收不到按键，用户侧看到的就是
  “快捷键时灵时不灵、只能重启程序”。现在改为**双层后端**：
  1. 首选 Windows 原生 `RegisterHotKey`（`managers/hotkey_native.py`）：系统解析
     按键后直接投递 `WM_HOTKEY`，没有钩子超时这一说，快捷键串写法与原来一致；
     本机实测默认的 `alt+z` 已被其他程序注册走（`ctrl+alt+z` 等组合可注册），
     过去这一冲突完全看不见，现在会退回钩子并在日志/设置窗口明确提示。
  2. 原生注册被占用时退回 keyboard 钩子，并配上**看门狗**（`managers/hotkey.py`）：
     每 15s 检查监听线程存活与注册是否还在，发现监听线程已退出就重建监听器、
     清掉“按下未抬起”的残留按键状态（残留会让快捷键组合永远匹配不上）并重新
     注册；此前这两种故障都只能靠重启程序恢复。
- **按键回调里的异常会让快捷键永久失联**：keyboard 库的处理线程没有异常保护，
  回调抛异常会让该线程直接退出，此后所有热键静默失效。回调现在只做线程切换，
  日志与截图调度全部搬到主线程执行（`_on_hotkey_pressed` → `_fire`），
  并加了 0.3s 防抖（钩子后端长按会连发，一次连发叠出好几张遮罩）
  （回归测试 `test_hotkey_callback_isolation`、`test_hotkey_parse`、
  `test_hotkey_backend_fallback_and_watchdog`）
- **残留/已销毁的遮罩引用会“吞掉”按键**：按下热键时只要 `_screenshot_widget`
  非空就当成“取消”，而引用可能只是残留（窗口已关闭或已被销毁），
  表现为热键按了没反应。现在先探测窗口是否真的还在显示：残留引用直接丢弃
  并正常开始本次截图（`managers/screenshot.py`，回归测试
  `test_screenshot_stale_widget`）

### 变更
- **遮罩覆盖整个虚拟桌面：多显示器一起冻结**：以前遮罩只盖主屏，副屏既不在
  冻结范围内（内容继续刷新），也框选不到副屏上的内容。现在按快捷键时抓拍整张
  虚拟桌面（`ImageGrab.grab(all_screens=True)`）、遮罩窗口铺满所有显示器，副屏
  也能直接框选；磁吸除桌面外沿外还认每块显示器的边界（跨屏接缝也是常用对齐线），
  操作提示条落在鼠标所在的那块屏上，钉图坐标按桌面原点换算（副屏在主屏左侧/
  上方时桌面原点为负）。各屏像素倍率不一致（混合 DPI 缩放）时退回主屏，
  避免单一缩放系数把坐标换算错（`managers/screenshot.py`，回归测试
  `test_screenshot_overlay_multi_screen`、`test_screenshot_snap_screen_seam`）。
  注：Pillow 的 `bbox` 是屏幕坐标，会被减掉虚拟桌面原点，整桌面抓拍必须不传
  `bbox`（多屏时传 `bbox=(0,0,宽,高)` 会裁到主屏内容再补黑边）
- **默认配置只在内存里用，不再凭空留下 `config.ini`**：以前首次启动会按默认值
  写一份 `config.ini` 出来，用户什么都没改也留下一个文件；现在默认值只在内存
  生效（每个选项本来就有 fallback），**改过设置才落盘**。文件存在但内容读坏时
  仍会就地修复，不会每次启动都回退默认值（`config.py`，回归测试
  `test_config_defaults_in_memory`）

### 性能
- **空选区的反馈快一倍**：本地引擎已判定“这张图没字”时，文本链路不再重跑一遍
  识别（Tesseract 单次约 0.6s，其中大头是模型加载），实测“框了个空区域”
  从 1.14s 降到 0.57s；云端引擎的空结果可能只是限流，仍走完整降级链
  （`managers/ocr.py`，回归测试 `test_ocr_empty_result_shortcut`）
- **逐块兜底翻译改为并发**：译文行数与块数对不齐时会逐块重译，原来串行发
  最多 8 个请求（实测 8 块 2.15s），现在并发 4 路、实测 8 块降到 0.82s、
  6 块 0.40s；单块失败只影响该块（`managers/screenshot.py`，回归测试
  `test_translate_blocks_parallel`）
- **启动预热翻译链路**：首个请求要付 DNS+TLS 握手（约 0.3s），启动后 0.8s
  用当前免费接口发一次短请求把连接池捂热，第一次截图直接省掉这一截；
  付费接口（LLM/百度/有道）不预热，不替用户花额度
  （`managers/translation.py::warm_up`，回归测试 `test_translation_warm_up`）

### 测试
- 修复 `tests/test_features.py` 里一个比失败更难查的坑：用例各自创建
  `QApplication` 且不持有引用，它被 GC 之后底层 C++ 对象随之销毁，
  再构造任何 `QWidget` 会触发 Qt 的 `qFatal`——进程**静默终止**（无异常、
  无回溯）。现统一由 `_ensure_qt_app()` 创建并长期持有引用。

## [Unreleased] — 重试策略、坐标回映射与覆盖外扩修复

### 修复
- **主引擎没走自己的重试策略**：`_default_retry_handler()` 从未被覆盖，主引擎
  一直落在基类通用的 3 次/1s 策略上（退避 1s+2s，一次瞬时故障先白等约 3 秒
  才降级，还多打一次请求；云端翻译/OCR 是靠额度与计费的），而块级链路、
  降级链路用的却是 2 次/0.5s。现在 OCR 与翻译两个管理器都显式指定各自的重试器，
  主引擎退避从约 3.0s 降到约 0.5s、请求数 3 → 2（`managers/ocr.py`、
  `managers/translation.py`，回归测试 `test_primary_retry_policy` ×2）
- **就地覆盖的译文与原文错位**：块级识别的坐标只按 `image.width /
  processed.width` 缩回原图，漏算了预处理为了防截断而加的四周 12px 留白——
  留白图上满足 `x_pad = x_orig * factor + pad`，正确回映射要先减 pad 再除以
  factor。偏差为 `pad / factor`，随横向位置线性变化（左端右偏、右端左偏，
  中心恰好抵消）：2 倍放大时两端各偏约 6px，靠 2px 的覆盖外扩才没醒目地
  露馅。真实截屏实测（`scratch/probe_align.py` + `scratch/align_check.html`）：
  三行文本横向偏差从 +5/+2/-3px 降到 0/0/0px，原文字迹从“边缘露出”变为
  “完整覆盖”（`managers/ocr_providers/tesseract.py`，回归测试
  `test_block_coords_round_trip`）
- **大字号就地覆盖盖不住原文边缘**：覆盖块外扩一直是固定 2px，而字号越大
  笔画越粗、抗锯齿（含 ClearType 彩色边）外溢越宽——实测真实截屏上，
  固定 2px 对 21px 行高的可见字迹外露余量为 **0px**（再宽 1px 就露出原文），
  33px 行高也只剩 1px。改成横向按行高比例外扩（`行高 × 0.12`，夹在 2~6px）：
  常见 UI 行高仍是 2px（不改变原有观感），大字号行高 33px 的余量从 1px 提到
  3px。纵向不跟着变（行框本身已含升部/降部余量，实测字迹距框顶 2px、
  距框底 5~7px），免得向上去吃上一行的译文；译文起点仍锚在原文左边界，
  外扩变宽不会把译文推歪（`managers/result_render.py`，回归测试
  `test_cover_pad_adaptive`）
- **反复打开设置会重建窗口、丢掉未保存的输入**：托盘双击/菜单「设置」每次都
  新建一个 `ConfigWindow`，旧窗口被销毁——刚粘进去还没点保存的密钥/URL
  连同滚动位置一起消失，窗口位置也回到默认。现在已打开时只置前复用同一实例
  （`main.py`，回归测试 `test_config_ui` 第 12 项）

### 测试
- 新增**像素级就地覆盖回归** `test_inplace_cover_pixel_alignment`：离屏把四行
  真实文本渲染成真像素（真字体引擎，不弹窗不抓屏）→ 真 Tesseract 识别 →
  真 `paint_blocks` 绘制，然后逐像素确认原文的每一个可见像素（含抗锯齿彩边，
  阈值 250）都落在绘制出的覆盖块里，并核对块框与字迹偏差 ≤2px、大字号行的
  横向外扩确实按行高放宽。把原先只能手跑的 `scratch/probe_align.py` 变成了
  套件里自动跑的用例（引擎/中文训练数据缺失时跳过），已反向验证能抓住“坐标
  漏算留白”（报 dx=5）与“外扩退回固定 2px”两种回归。保留了 scratch 里的
  探针作为抓真屏 + 出视觉报告的对照工具

## [Unreleased] — 小图识别修复与体验修复

### 修复
- **截图选区太小翻译不出来**：固定 2 倍放大对 20~30px 高的小选区远远不够——
  里面的中文只有约 12px，再叠加高斯模糊与固定 128 二值化，Tesseract 几乎
  必然识别为空。预处理改为：**小图自适应放大 2~4 倍（目标高约 64px）**、
  去掉高斯模糊与固定阈值（改为 autocontrast + Otsu 自适应二值化，深浅主题
  都能保住字迹）、二值化后四周留白防止字符贴边截断。实测真实屏幕 12~20px
  的 ClearType 中文，6 个用例从旧管线 2 个正确提升到 5 个全对
  （`managers/ocr_providers/tesseract.py`，回归测试 `test_preprocess_image` /
  `test_scale_factor` / `test_otsu_threshold`）
- **OCR 降级提示文本被送去翻译**：空白选区或引擎不可用时返回的
  “未识别到文字…”/“[OCR识别不可用…]”提示被当作原文调用翻译 API，
  白等一次网络请求后才把提示文本当译文展示；现在识别到提示文本直接
  跳过翻译阶段立即展示（`managers/screenshot.py`，回归测试
  `test_ocr_notice_not_translated`）
- **托盘双击防抖窗口过短**：防抖窗口上限 0.50s 恰好等于系统双击间隔默认值
  500ms，按系统速度双击时第二击事件几乎必然落在定时器触发之后，被误判为
  新一轮单击，造成“截图开了又关”。窗口改为系统间隔+50ms 余量（夹取到
  0.30~0.70s），并增加迟到第二击识别：武装后又有激活则改开设置
  （`managers/tray.py`，回归测试 `test_tray_debounce`）

### 清理
- 移除死代码与未用导入（`window_enum` 的占位赋值、`error_handler`/
  `pin_window`/`result_render`/`providers/llm` 的未用变量与导入），
  全库通过 pyflakes

## [Unreleased] — 界面主题（含浅色/白色主题）与代码审核修复

### 新增

#### 界面
- **浅色（白色）主题**：`managers/styles.py` 重构为调色板驱动的 QSS 生成器（`dark` / `light`），
  配置窗口新增“外观”分组下拉框；**切换立即预览，点“保存”后长期生效**，
  主题持久化在 `[UI] theme`（新增配置段，旧配置自动迁移补齐）
- **控件样式补齐**：滑条（轨道/已选段/圆角手柄）、滚动条、工具提示、下拉弹层、
  消息框在两套主题下都成套成对，浅色底上不再出现看不清的默认灰色控件
- **设置页滚动区**：“保存”按钮固定在滚动区下方，配置项变多后小屏设备也能完整操作

### 修复
- **钉图窗口按任意键报错**：`pin_window` 误用 `event.Copy`（`QKeyEvent` 上没有该属性）
  会在任何按键时抛 `AttributeError`；改为 `QKeySequence.Copy`，并补齐钉图的 Ctrl+C 复制
- **写 OCR 选项会重置遮罩参数**：`set_ocr_option` 在 `[OCR]` 段缺失时调用了整段默认值函数，
  把用户调好的 `snap_threshold`/`dim_opacity` 覆盖回 12/90；配置段默认值函数已解耦（回归测试固化）
- **扩展 provider 的配置存不下来**：配置窗口动态字段走硬编码 getter/setter 表，
  插件自己声明的字段读不到、写不进配置（与“插件无需改主程序”的承诺不符）；
  新增 `ConfigManager.get/set_translation_option` 通用通道，UI 回归用探针 provider 验证保存与回读
- **配置类错误被无意义重试**：未填密钥、引擎不支持能力、本地引擎识别不到文字，
  原本也要等两次指数退避（约 2–4 秒）才降级；新增 `NonRetryableError` 分类
  （`ProviderConfigError` / `ProviderUnsupportedError` / `ProviderEmptyResultError`），
  空白选区识别 **2.1s → 0.6s**，且提示从“OCR识别不可用，请检查配置”改为
  “未识别到文字，请重新框选包含文字的区域”
- **配置写盘改为原子替换**：先写同目录临时文件再 `os.replace`，写到一半被中断不再
  留下半截文件导致下次启动整份回退默认值（密钥、快捷键不会丢）
- **一次“保存”写盘 9 次 → 1 次**：新增 `ConfigManager.batch()` 批量上下文，
  配置窗口的十几个 setter 合为一次落盘
- **重译多打一次请求**：`_translate_blocks` 复用已算好的整段译文，
  跳过缓存重译时不再对同一段文本重复发请求
- **诊断信息全部丢失**：`hotkey.py`/`tray.py`/`config.py` 的 47 处 `print` 改为 `logger`——
  打包后没有控制台，print 全部看不到（热键注册失败曾经完全无痕，只能靠猜）
- 清理未使用导入与裸 `except`（钉图窗口、LLM provider、主应用退出路径）

### 变更
- 配置新增 `[UI] theme = dark|light`（默认 `dark`，不影响既有用户观感）

## [Unreleased] — 上一轮迭代

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
- 配置窗口更名"设置"，启动默认最小化到托盘；**托盘提示通知只在首次运行弹一次**
  （文案精简为 `Alt+Z 开始截图翻译`），之后驻留托盘不再打扰，交互方式看图标悬停提示
- 旧配置文件自动迁移：自动补齐 `[OCR]`、`[Overlay]` 段
- **仓库只保留源码**：Tesseract 便携包+语言数据（约110MB）不入库；
  `scripts/make_release_package.py` 打为分卷 zip（≤90MB/卷）上传 Gitee Release 附件，
  克隆端 `scripts/setup_ocr.py` 自动下载、SHA256 校验、解压，支持 `--local` 离线恢复
- **分发包改为 onedir 目录形态**：`screenshot-translator.spec` + PyInstaller 打出
  `dist/ScreenshotTranslator/`（含图标、无控制台、内置Tesseract OCR与中英文语言包，
  开箱即用，整目录约297MB）；新增 `managers/app_paths.py` 统一双形态路径定位——
  打包态只读资源走 `_MEIPASS`（onedir 下即 `_internal`），可写文件见下条
- **可写数据迁到用户目录 `~/.screenshot-translation`**：打包态的 `config.ini`、
  `translation_cache.json` 与日志不再写在 exe 旁边，产物目录保持只读——
  装进 Program Files 或整目录覆盖升级都不会丢配置；`migrate_legacy_data()`
  把旧版留在 exe 同目录的配置与缓存首次启动搬进用户目录（只补缺、不覆盖、不删原件），
  源码运行仍写在项目根，不打扰开发者本地文件
- **OCR 瘦身打包**：`scripts/collect_ocr_bundle.py` 抽取运行必需的
  tesseract.exe+DLL+eng/chi_sim（排除约60MB训练工具），打包时自动收集最小集

### 性能与资源
- **启动 2.97s → 0.44s**：onedir 不再把 486MB 内容解压到 `%TEMP%\_MEI*` 后才运行，
  强制结束进程也不再遗留解包目录（此前每次异常退出留一份）
- **依赖去重裁剪**：tesseract 作为二进制数据收集时被依赖分析重复落到根目录，
  按真实 PE 导入表核实后仅保留 `libffi-8.dll`、`zlib1.dll`，删掉 47 个重复 DLL；
  同时剔除 Qt 的 QML/场景图/Vulkan/翻译包与 PIL 的 `_avif/_webp/_imagingtk` 等
  用不到的模块（binaries 212→157、datas 110→35）
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
- 新增打包态数据目录用例（模拟 `sys.frozen`/`_MEIPASS` 与临时 HOME，验证可写路径、
  只补缺迁移、源码形态不变），`pytest tests -q` 21 passed

## [历史版本]

初始版本：截图 → Tesseract OCR → 翻译（LibreTranslate/必应/百度/有道）基础流程，含缓存、重试、进度显示、托盘与全局热键。
