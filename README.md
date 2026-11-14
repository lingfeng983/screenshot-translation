# 截图翻译工具 - 使用说明

> 📝 更新日志见 [CHANGELOG.md](CHANGELOG.md)

## 🚀 快速开始

### 方式一：安装程序（推荐普通用户）
从 [Releases](https://github.com/lingfeng983/screenshot-translation/releases) 下载
`ScreenshotTranslator-Setup.exe`（约 83MB，含离线识别模型），双击安装：

1. 安装包已经包含 RapidOCR 的识别模型，**不再需要下载任何 OCR 组件**，
   装完即可断网识别
2. 装完托盘出现图标（若被折叠，点任务栏 `^` 展开并右键图标→设置常显）
3. 按 `Alt+Z` 截图翻译；按 `Ctrl+Alt+Z` 只截图不翻译（进入带标注的截图编辑态）；
   `Ctrl+Alt+F` 全屏截图、`Ctrl+Alt+W` 活动窗口截图、`Ctrl+Alt+R` 重复上次区域

> 本地识别由 RapidOCR + ONNX Runtime 在程序内完成，不依赖系统安装 Tesseract；
> OCR 引擎固定为本地 RapidOCR，设置里不再提供引擎选择。

### 方式二：源码运行（开发者）

#### 首次安装
```bash
# 1. 克隆仓库
git clone https://github.com/lingfeng983/screenshot-translation.git
cd screenshot-translation

# 2. 安装依赖（加 -c constraints.txt 可复现发版时验证过的精确版本）
pip install -r requirements.txt -c constraints.txt
#    开发/发版另装：pip install -r requirements-dev.txt -c constraints.txt

# 3. 创建本地配置（含API密钥，不入库）
copy config.example.ini config.ini

# 4. 启动（Windows 10/11；本项目依赖随 Python 3.14 验证，3.10+ 可跑）
python main.py
```

> **离线识别说明**：RapidOCR 的 PP-OCRv6 模型（约 30MB）随 `rapidocr` 依赖一起装好，
> 在程序进程内经 ONNX Runtime 推理，源码态与安装包都不需要额外下载识别组件，
> 也不需要系统装 Tesseract。模型文件缺失时会提示本地 OCR 组件不完整。

### 启动程序
仓库里没有启动批处理脚本，装好依赖后直接运行：

```bash
python main.py
```

> 若机器上装了多个 Python，请用装了依赖的那个解释器（例如 `py -3.14 main.py`），
> 或先建并激活虚拟环境；PATH 上默认的解释器缺 pytest/PyQt5 时会报导入错误。

## 🎯 主要功能

### 1. 系统托盘操作
程序启动后在系统托盘显示图标，交互方式：
- **左键单击 / 双击图标** - 打开文本翻译页（重复点击只会把已打开的窗口置前）
- **右键图标** - 菜单（开始截图翻译 / 仅截图（不翻译） / 全屏截图 / 活动窗口截图 /
  重复上次区域 / 文本翻译 / 检查更新 / 设置 / 退出）

### 2. 快捷键操作
- **翻译快捷键**: 默认 `Alt+Z`（`global_hotkey`）— 框选后识别并就地翻译
- **区域截图快捷键**: 默认 `Ctrl+Alt+Z`（`capture_hotkey`）— 框选后进入截图编辑态
- **文本翻译快捷键**: 默认 `Ctrl+Alt+T`（`translate_hotkey`）— 打开文本翻译页
- **全屏截图**: 默认 `Ctrl+Alt+F`（`fullscreen_hotkey`）— 整块虚拟桌面直接进编辑态
- **活动窗口截图**: 默认 `Ctrl+Alt+W`（`window_hotkey`）— 抓当前前台窗口
- **重复上次区域**: 默认 `Ctrl+Alt+R`（`repeat_hotkey`）— 用上次确认的选区再截一次
- 截图类快捷键都不跑翻译、不发起网络请求；六条可在设置面板中分别自定义，
  **任意两条不能重复**（否则一次按键会同时触发多个动作）
- 注册走 **Windows 原生 `RegisterHotKey`**（系统直接投递按键，不受键盘钩子
  超时/被摘的影响）；该组合已被其他程序注册走时自动退回键盘钩子，
  并**弹托盘通知 + 写日志 + 在设置窗口提示**你换一个组合；占用是暂时的，
  对方程序退出后会自动切回原生注册（每 15s 重试一次）

### 3. 截图翻译流程
1. 按快捷键或点击托盘菜单启动截图
2. 按下的瞬间整屏就被**冻结成一张快照**（视频、时钟、进度条都停在那一刻，框选和看译文时都不会再动，框到的画面与最终识别的就是同一帧）；
   **多显示器时整张虚拟桌面一起冻结、一起覆盖**，副屏上的内容也能直接框选
3. 在屏幕上拖拽选择要翻译的区域（选区会自动磁吸对齐到附近窗口边缘与屏幕边，蓝色虚线指示吸附目标）
4. **松开鼠标立即开始识别**（不需要二次确认）；遮罩保持不关闭，选区中央只显示一个
   **小转圈**（不写"识别并翻译中"这类文字，等待本身不需要解释）
5. 译文就地覆盖回原文位置（取原文周围的背景色填底、按块高自动缩字），遮罩不关闭
6. 结果出来后选区四角/四边仍可拖动调整范围（按住选区内部可整体平移）：**松开鼠标即按新范围
   重新识别翻译**，边调边看，不用退出重来
7. 选区下方浮动工具条可切换 **原文 / 双语 / 译文** 三态、换目标语言重译，
   还有六个图标按钮：**提取原文/译文文本到剪贴板**、**保存原图/译图**、**复制原图/译图到剪贴板**，
   以及钉住、重译、关闭；**提取或复制到剪贴板成功后遮罩自动关闭**
8. 鼠标悬停或单击选区内的段落，会在下方气泡卡片里显示该段原文与译文（Ctrl+C 只复制该段）
9. 行级坐标不可用时（OCR 引擎不支持或译文行数对不齐）自动降级为整段气泡卡片展示

多显示器说明：各屏缩放比例一致时覆盖全部显示器（副屏在主屏左侧/上方也行）；
缩放比例不一致（混合 DPI）时只覆盖主屏，因为单一缩放系数无法把坐标换算准确。

遮罩内快捷键：`ESC` 退出、`T`/`1`/`2`/`3` 切换展示模式、`Ctrl+C` 复制、
`Ctrl+S` 保存结果图、`P` 钉屏、`R` 失败后重试；结果态下再按一次截图热键等同于取消。
钉住的窗口支持拖动、滚轮缩放、双击关闭、`Ctrl+C` 复制图片。

### 4. 纯截图（不翻译）
按 `Ctrl+Alt+Z`（或托盘菜单「仅截图（不翻译）」）进入完整的截图工作流，
**全程不发起网络请求**，也默认不跑 OCR。

**框选阶段**（沿用翻译截图的整屏冻结与多屏/DPI 处理，并补上主流工具的辅助）：
- 窗口自动识别：鼠标悬停会高亮所在的整个窗口，**单击即选中该窗口**，无需拖拽
- 放大镜：跟随光标显示 7× 放大画面与 `坐标 / RGB / #RRGGBB`，配合边缘磁吸精确对齐
- 也可跳过框选：`Ctrl+Alt+F` 全屏、`Ctrl+Alt+W` 活动窗口、`Ctrl+Alt+R` 重复上次区域

**编辑阶段**（松手后不立即出图，先进入编辑态）：
- 十种工具：选择/移动选区、矩形、椭圆、直线、箭头、画笔、荧光笔、文字、序号标记、马赛克
- 8 色色盘 + **屏幕吸管取色**，线宽 2~8 px 逐像素一档、字号 8~32 px 每 2 px 一档；
  `Ctrl+Z` 撤销、`Ctrl+Y` 重做、清空（垃圾桶图标，与取消的红叉区分）
- 标注默认仍是**烧录语义**（画上即合成进像素），但**最近一次落笔的图元保持可编辑**：
  可拖动移动、拖手柄缩放、滚轮调线宽/字号，点别处取消选中；橡皮擦可擦掉自己画的图元。
  更早的图元不再能单独选中，靠 `Ctrl+Z` 整步撤销
- 选区四边四角都能拖拽缩放，也能整体平移；**移动或缩放后按新范围重新取景**，
  旧标注属于旧画面会一并清空（有提示），不会把旧内容搬到新位置上
- **提取文字**：工具条「取字」用内置本地 RapidOCR 识别图中文字，按阅读顺序整理后
  自动写入剪贴板并弹出可编辑结果窗（本地推理，不联网）
- **长截图**：工具条「长截图」以选区为固定视口，**由你自己滚动页面**，程序不注入
  任何滚轮消息。后台定时抓帧，用相邻帧的条带模板匹配求重叠量拼成一张长图；
  选区外的横幅实时显示已拼接高度与段数，滚太快出现断层或往回滚都会给出提示。
  点横幅「完成」（或再按一次截图快捷键）结束并复制到剪贴板，点「取消」丢弃。
  **完成后还会打开独立的长图结果预览窗**（等比缩放看全图、滚轮/Ctrl+滚轮缩放，
  可在原图上复制、保存）；快速保存开启时同时按 `[Capture]` 配置自动存盘。
  结果窗是非模态的，遮罩关闭后仍可继续操作。
  滚动期间遮罩会暂时隐藏，否则它会盖住要滚动的页面、也会被拍进长图

**输出**：`Ctrl+C` 复制、`Ctrl+S` 保存、`P` 钉到屏幕、`ESC` 退出；工具条另有
取消与上述动作按钮。钉住的窗口支持拖动、滚轮缩放、双击关闭，右键菜单可复制、
保存、切换置顶与透明度。

- 保存走 `[Capture]` 配置：PNG / JPG / WebP、JPG 质量、默认目录、
  文件名模板（strftime）、快速保存（不弹文件框）、保存后动作（复制/关闭）。
  安装包裁剪掉了 WebP 编码插件，选 WebP 时会自动回落为 PNG（同 `config.example.ini` 说明）
- 复制到剪贴板成功后遮罩自动收起
- 设置页「纯截图（不翻译）」分组里可调整以上全部参数与长截图参数

**快捷键映射**：`Ctrl+C` 复制合成图、`Ctrl+S` 保存、`P` 钉住、`Ctrl+Z` 撤销、
`Ctrl+Y` 重做、`ESC` 取消。

### 5. 文本翻译页（输入翻译）
不想截图时，左键托盘图标或按 `Ctrl+Alt+T` 打开"文本翻译"：左边输入或粘贴文本，
右边看译文，同样走当前配置的翻译接口与降级链（与截图翻译共用缓存）。

- **输入或粘贴结束后自动翻译**（停止输入约 0.6 秒触发），不需要再点按钮；
  "翻译"按钮保留，`Ctrl + Enter` 可立即翻译
- 右上角可单独切换目标语言（只影响本窗口，不改全局设置）；切换语言会自动重译
- 译文只读可选中，"复制"按钮一键写入剪贴板
- 翻译期间"译文"旁只有一个小转圈，按钮置灰，不会卡住窗口

### 6. 更新检查与推送
- 启动约 6 秒后在后台查询 GitHub 最新正式版（`releases/latest`），之后每 6 小时
  再查一次，托盘常驻期间也能收到提醒，不阻塞界面
- 发现新版本时弹**托盘通知**（同一版本只推一次）；右键托盘「检查更新」可随时手动检查
- 确认更新后**全程在程序内完成**：自动下载安装包（带进度、可取消）→ 校验大小、
  可执行文件头与 GitHub 附件的 sha256 摘要（缺失/算法不对直接拒绝）→ 校验发布者
  签名 → 静默启动安装程序并退出本体 → 装完自动重新打开。不需要去发布页手动下载；
  发行版没挂安装包时会直接提示"暂无可下载的安装包"
- 远端 `tag_name`、附件名、下载直链都会先做白名单校验，下载目录再做越界检查，
  避免被伪造的 Release 响应把文件写到临时目录之外或读到本地文件
- 下载先用 requests（跟随重定向、读环境代理），证书链校验不过时改用系统信任库
  重试，两条路都失败才报错——不会为了"能装上"而跳过证书校验
- 发版时若配置了代码签名（见「打包」），客户端会强制核对签名状态与发布者证书指纹；
  未签名的历史发行版仍只按强制 sha256 摘要放行，并在日志里给出告警
- 设置页「启动时检查更新」可关闭自动检查（手动检查始终可用）
- 当前版本来自 `managers/app_version.py`，发版脚本会自动写入本次版本号

## ⚙️ 配置说明

### 翻译API设置
支持多种翻译服务（在配置窗口的下拉框中选择，并可为每个API填入对应密钥）：
- **Edge翻译(免费)** (默认) - Microsoft Edge 内置翻译同款免费接口，微软翻译服务国内可直连，无需任何密钥
- **谷歌翻译(免费)** - Copyfish 扩展同款免费接口，无需密钥，但国内裸连不可达（有代理时可用）
- **LibreTranslate** - 免费开源翻译，可自建服务，可自定义API地址
- **必应翻译** - 需要 API 密钥
- **百度翻译** - 需要 应用ID + API密钥
- **有道翻译** - 需要 应用ID + API密钥

还可配置：
- **目标语言** - 中文 / 英语
- **降级API** - 主API失败后自动切换的备用API。默认情况下降级链到此为止，
  **不会**再自动把原文转给 Edge / 谷歌免费接口；需要该行为请在设置页「隐私」分组
  勾选「主接口失败时自动改用免费接口」（或设 `allow_free_fallback = true`）
- **测试翻译** - 保存前先验证当前API配置是否可用（后台线程执行，不会卡住设置窗口）
- **截图遮罩** - 磁吸阈值（`snap_threshold`，0~48px，选区吸附窗口边缘的距离）与暗角透明度（`dim_opacity`，0~180），滑条调节，下次截图生效

### 隐私与数据
设置页「隐私」分组集中管理四个开关，默认按最小外发/最小留存配置：

| 开关 | 默认 | 说明 |
| --- | --- | --- |
| 保存翻译缓存 | 开 | `translation_cache.json` 以**明文**保存截图识别出的原文与译文，24 小时过期、最多 1000 条；关闭时会立即清掉内存与磁盘文件 |
| 退出时清空缓存 | 关 | 每次退出清空，磁盘上不留明文 |
| 免费接口兜底 | 关 | 开启后主接口失败才会把原文自动发给 Edge / Google 免费端点 |
| 启动预热 | 关 | 开启后在启动时向当前免费接口发一次固定的 `Hello` 请求，换取首译更快 |

- **立即清空翻译缓存**按钮随时清除已落盘的明文，不需要重启。缓存文件与
  `config.ini` 一样在写盘前收紧权限（POSIX 下 `0o600`），避免同机其它用户读到明文。
- API 密钥优先从环境变量读取（`BING_API_KEY` / `BAIDU_API_KEY` / `BAIDU_APP_ID` /
  `YOUDAO_API_KEY` / `YOUDAO_APP_ID`）；在设置页手填的密钥会以**明文**写入
  `config.ini`，该文件已排除出版本库与安装包，建议改用环境变量注入。
- 谷歌免费接口用表单编码的 **POST** 把原文放在请求体，不再拼进 URL 查询串
  （查询串会被代理/CDN/访问日志原样记录）。
- 配置文件损坏时会先备份成带时间戳的 `config.ini.<时间>-<PID>.bak` 再覆盖，
  并通过托盘通知告知备份位置；旧版留在安装目录的明文配置/缓存迁移成功后会删除，
  删不掉时给出明确告警。
- 自动更新强制校验 sha256 摘要、净化远端标签与路径，并支持强制核对
  Authenticode 发布者指纹（见「打包」）。

### OCR识别
识别引擎**固定为本地 RapidOCR**，代码里写死，设置窗口不再提供引擎选择：
- 内置 PP-OCRv6 检测/识别/方向分类模型，经 ONNX Runtime 在进程内推理
- 完全离线、免费，无需填入任何密钥，也不用安装外部 OCR 程序
- 识别阈值可在 `config.ini` 的 `[OCR] rapidocr_score_threshold` 调整（默认 0.5）
- `mock` 模拟引擎仅作为程序内部的兜底保留（组件异常时返回提示文本），用户无需关心

扩展新的OCR引擎与翻译API方式相同：在 `managers/ocr_providers/` 下新建定义了 `register(registry)` 的模块即可（不影响默认走本地 RapidOCR）。

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

### 界面风格
界面固定使用**浅色（白色）**风格，不再提供主题切换。截图遮罩、结果卡片与
遮罩工具条是浮在屏幕内容之上的深色元素，风格独立（白底卡片在亮屏幕上会看不清）。

### 快捷键设置
可在配置面板中自定义**六条**全局快捷键（截图翻译、区域截图、文本翻译、全屏截图、
活动窗口截图、重复上次区域），任意两条都不能设成同一组合。每个快捷键各有一行
实时状态：注册成功会写明走系统投递还是键盘钩子；被别的程序占用时直接标红提示
"原生注册失败，已退回键盘钩子"，并按日志里的原因提示如何释放占用。开窗、改组合、
点保存都会刷新这六行。

### 纯截图设置
设置页「纯截图（不翻译）」分组对应 `[Capture]` 配置段：保存格式（PNG/JPG/WebP）、
JPG/WebP 质量、默认保存目录、文件名模板（`strftime`，例如 `截图_%Y%m%d_%H%M%S`）、
快速保存（跳过文件对话框）、保存后动作（仅保存 / 保存并复制 / 保存并关闭 /
保存、复制并关闭）、框选放大镜开关，以及长截图的抓帧间隔与长图最大高度。
标注的默认颜色、线宽与字号会在工具条上调整后自动记住，无需手改配置。

### 单实例
同一时间只允许运行一个实例（命名互斥体 `Local\ScreenshotTranslator.SingleInstance`）。
重复启动只会弹一句"已经在运行中，请用托盘图标或快捷键"然后退出，避免出现两套托盘
图标、两个全局热键互相抢占、后启动的进程覆盖前一个写好的缓存。开发/联调确实要多开时：

```bash
set ST_ALLOW_MULTI_INSTANCE=1 && python main.py
```

### 启动行为
- 启动后最小化到托盘（默认）
- 或启动时显示配置窗口
- **开机自启动**（默认关闭）：设置页勾选后写入当前用户注册表 Run 项
  （`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`），不需要管理员权限；
  打包态指向 exe，源码态用 `pythonw` 拉起 `main.py`，升级覆盖安装后启动时会
  自动校正到新的 exe 路径

## 🔧 故障排除

### 常见问题及解决方案

#### 1. OCR功能不可用
**现象**: 选区内提示"[OCR识别不可用：请检查本地OCR组件是否完整]"
**原因**: 本地识别组件缺失/损坏（极少见），通常是 `rapidocr` 或 ONNX Runtime 安装不完整
**解决**:
- 重装安装包（模型随包分发）；源码运行则 `pip install -r requirements.txt` 重装依赖
- 仍失败时查看日志里的具体异常（源码运行在项目根，打包运行在 `%USERPROFILE%\.screenshot-translation\`）

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
- **Python版本**: 3.10+（当前发布包用 3.14 构建）
- **必需依赖**: PyQt5, Pillow, rapidocr, onnxruntime, numpy, opencv-python-headless,
  requests, pystray, keyboard

### 目录结构
```
ScreenshotTranslation/
├── main.py              # 主程序入口（含设置窗口）
├── config.py            # 配置管理（[Translation]/[OCR]/[Overlay]/[Capture]/[Hotkey]/[Startup]/[Update]）
├── config.ini           # 配置文件（改过设置才生成，旧版本自动迁移补齐新段）
├── CHANGELOG.md         # 更新日志
├── requirements.txt     # 依赖列表（跑程序，兼容范围）
├── requirements-dev.txt # 开发/发版工具（pytest / ruff / PyInstaller）
├── constraints.txt      # 已验证的精确版本（配合 -c 复现）
├── pytest.ini / pyproject.toml  # 测试标记、ruff / mypy / coverage 配置
├── .github/workflows/ci.yml     # CI：语法检查、ruff、mypy、全量测试
├── screenshot-translator.spec  # PyInstaller onedir 打包配置（含依赖裁剪）
├── scripts/
│   ├── publish_release.py       # 一条命令出包＋发版（测试→构建→Setup→发 Release）
│   ├── setup.iss                # Inno Setup 7 安装脚本（OCR 模型已随包，不再下载）
│   ├── rt_preload_onnx.py       # PyInstaller 运行时钩子：先导入 onnxruntime 再加载 Qt
│   └── pyi_preload/             # 构建期隔离子进程的 sitecustomize（同一导入顺序约束）
├── tests/                   # pytest 回归测试（从项目根直接 python -m pytest tests/）
│   ├── conftest.py                  # 共用装置：sys.path、会话级 QApplication、online/smoke 标记
│   ├── _runner.py                   # 脚本运行器的用例发现（python tests/test_xxx.py 时走它）
│   ├── test_translation_providers.py  # 翻译架构测试
│   ├── test_ocr_providers.py          # OCR架构测试
│   ├── test_features.py               # 基础功能测试
│   ├── test_capture_features.py       # 纯截图升级专项（标注/长截图/取字/快捷键/配置）
│   ├── test_config_ui.py              # 配置界面 + 遮罩结果态测试
│   └── test_single_instance.py        # 单实例互斥（仅 Windows）
├── managers/                # 核心功能模块
│   ├── provider_common.py   # ★ 插件架构公共基座（注册表/自动发现/基类/降级链）
│   ├── app_paths.py         # ★ 路径定位（源码→项目根 / 打包→~/.screenshot-translation，含旧数据迁移）
│   ├── single_instance.py   # ★ 命名互斥体单实例守卫（重复启动时提示并退出）
│   ├── translation.py       # 翻译管理器（分发/缓存/降级链）
│   ├── ocr.py               # OCR管理器（分发/重试/降级）
│   ├── screenshot.py        # 截图遮罩（磁吸/气泡/角标记/结果态拖动调整选区）+ 就地译文结果态
│   ├── result_render.py     # ★ 结果渲染（行块贴图覆盖/气泡卡片/裁剪合成）
│   ├── text_block.py        # ★ OCR行块模型 + 段落聚合（悬停/选块依据）
│   ├── overlay_toolbar.py   # ★ 翻译结果浮动工具条（三态/语言/六个图标操作/钉住/重译/关闭）
│   ├── capture_annotation.py# ★ 截图标注图元（烧录语义，离屏可测）
│   ├── capture_editor.py    # ★ 编辑态控制器（状态机/事件分发/撤销栈/合成导出）
│   ├── capture_toolbar.py   # ★ 编辑态工具条（十种工具/色盘/线宽字号/取字/长截图）
│   ├── capture_magnifier.py # ★ 框选放大镜与屏幕取色
│   ├── capture_output.py    # ★ 截图输出（格式/质量/目录/模板/快速保存）
│   ├── capture_ocr.py       # ★ 本地 OCR 取字 + 可编辑结果窗
│   ├── capture_scroll.py    # ★ 长截图（手动滚动/选区抓帧/条带模板匹配/长图拼接）
│   ├── scroll_result.py     # ★ 长图结果预览窗（缩放/复制/保存）
│   ├── update_checker.py / update_installer.py  # 更新检查、下载校验与签名核对
│   ├── vector_icons.py      # ★ 程序化矢量图标（提取/保存/复制 + 标注工具）
│   ├── pin_window.py        # ★ 结果钉屏浮窗（可多开、置顶、右键菜单）
│   ├── styles.py            # ★ 浅色 QSS（调色板驱动）
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
│   └── ocr_providers/       # OCR引擎插件目录（扩展放这里）
│       ├── base.py          #   OCR基类（继承公共基座）
│       ├── rapidocr.py      #   本地RapidOCR（固定使用，ONNX Runtime 进程内推理）
│       └── mock.py          #   模拟OCR（组件异常时内部兜底）
└── resources/
    ├── icon.png / icon.ico  # 程序图标（构建时自动生成）
    └── config 等只读资源
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
# 1. 打包程序本体（含 RapidOCR 模型与 ONNX Runtime，整个目录约 248MB）
#    ST_VERSION 会被写进 exe 的 Windows 版本资源（右键属性看得到）；不给就没有版本信息
#    环境变量按你的 shell 给：PowerShell 用 $env:，cmd 用 set，Bash 才用 VAR=值 前缀
$env:ST_VERSION = "1.7.0"      # cmd: set ST_VERSION=1.7.0
python -m PyInstaller screenshot-translator.spec --noconfirm

# 2. 编译安装器（先把 dist/ScreenshotTranslator/ 的内容拷进 release/onedir/）
iscc.exe /DMyAppVersion=1.7.0 scripts\setup.iss
# 产物：release/ScreenshotTranslator-Setup.exe（约 83MB，唯一对外分发包）
```
> 上面两步 `python scripts/publish_release.py --version 1.7.0` 一条命令串完，
> 顺带提交、打 tag、发 Release。编译安装脚本需要 Inno Setup 7。

### 发版预检与代码签名
`publish_release.py` 在**任何写操作之前**先做全只读预检：干净工作树、CHANGELOG
是否有 `[Unreleased]`（没有会立刻退出，不会再出现"包都编完了才失败"）、tag 与
HEAD 一致、版本号常量可写、复用产物是否齐备、origin 是否为发布目标仓库。
提交时只 `git add` 显式清单（CHANGELOG、版本号、指纹文件），不会把工作区里
没审查完的改动带进发布。

发布包建议做 Authenticode 签名：设置环境变量 `ST_SIGNTOOL_COMMAND`，命令里用
`$f` 代表待签名的 Setup.exe，例如

```powershell
$env:ST_SIGNTOOL_COMMAND = 'signtool sign /fd sha256 /sha1 <证书指纹> /tr http://timestamp.digicert.com /td sha256 "$f"'
python scripts/publish_release.py --version 1.7.0
```

发版脚本编译后签名、验签，并把证书指纹写进 `resources/publisher_thumbprint.txt`
（随包分发、纳入提交）；客户端自动更新会强制核对签名状态与指纹。未配置签名时
只打印告警，仍按强制 sha256 摘要放行历史发行版。手工编译时请自行对
`release/ScreenshotTranslator-Setup.exe` 执行同一条签名命令。

RapidOCR 的 PP-OCRv6 检测/识别/方向分类模型（约 30MB）与 ONNX Runtime 都打进
`_internal/`，安装时不再下载任何组件、也不需要系统装 Tesseract：装完即可断网识别。
安装器用 `lzma2/ultra64` 压缩换取更小的下载体积（约 250MB 目录 → 约 83MB 安装包）。

打包时自动裁剪无用依赖：Qt 的 QML/场景图/Vulkan/ANGLE 与翻译文件、PIL 的
`_avif/_webp/_imagingtk` 等扩展、OpenCV 的视频后端与示例数据、RapidOCR 的
Paddle/PyTorch/OpenVINO/MNN 分支。另外本机组合（Python 3.14 + PyQt5 + onnxruntime）
存在导入顺序约束：onnxruntime 必须先于 Qt 加载，`main.py`、`scripts/rt_preload_onnx.py`
与 `scripts/pyi_preload/sitecustomize.py` 分别覆盖源码、冻结进程、构建子进程三种场景。

exe 启动即加载本目录下的 `_internal`，不再解包到临时目录。可写文件统一落在
`%USERPROFILE%\.screenshot-translation\`（`config.ini`、`translation_cache.json`、
按 2MB × 3 轮转的 `screenshot_translation.log`）——其中 `config.ini` 只在
你**改过设置之后**才生成，默认配置全程在内存里用，不会凭空丢一个文件出来。
产物目录保持只读——放进
Program Files 或整目录覆盖升级都不会丢配置；旧版本留在 exe 同目录的配置与缓存
首次启动会自动搬进该目录（只补缺，不覆盖已有文件），副本校验一致后会删除程序
目录里的明文原件，删不掉时打明确告警。源码运行仍在项目根。

### 测试
```powershell
# Windows 上先把输出编码设好（$env:PYTHONIOENCODING="utf-8"），否则中文用例会乱码
$env:PYTHONIOENCODING = "utf-8"
python -m pytest tests/ -q -rs                 # 全程离线，发版前必跑
$env:ST_RUN_ONLINE = "1"; python -m pytest tests/ -q -m ""   # 追加真连各翻译接口
$env:ST_RUN_SMOKE = "1"; python -m pytest tests/ -q -m ""    # 追加真拉起 main.py（会注册全局热键）
ruff check --select E9,F63,F7,F82 .           # CI 同款零误报静态检查
mypy -p managers                              # CI 同款类型检查（配置见 pyproject.toml）
python -m pytest tests/ --cov=managers --cov-report=term-missing  # 覆盖率
```

> 机器上装了多个 Python 时，用装依赖的那个解释器跑（如
> `py -3.14 -m pytest tests/ -q -rs`）；默认 PATH 上的解释器可能缺
> pytest/PyQt5。CI（`.github/workflows/ci.yml`）在 Windows + Python 3.12 上
> 依次跑依赖一致性检查、语法检查、ruff、mypy 与全量离线测试。
不跑 pytest 也能直接 `python tests/test_features.py`：用例由 `tests/_runner.py`
按模块命名空间发现（不再手抄名单），需要 pytest fixture 的那几条会列出名字而不是
静默跳过。开发/发版工具装在 `requirements-dev.txt`（pytest、pytest-cov、ruff、mypy、PyInstaller）。
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