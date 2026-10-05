# SnapTrans · 翻译放大镜

一个小的玻璃态浮窗，浮在英文内容上，OCR 识别后把中文**按原文排版原位显示**；内容变了自动重翻。专为 ZCode 这类没有内置翻译的桌面客户端设计（插件介绍、AI 对话、说明文档等）。

## 快速上手（两种方式任选）

- **绿色 exe**：直接双击 `dist\SnapTrans.exe`（无需 Python；config.json / glossary.txt 放在 exe 旁边即可）
- **脚本运行**：双击 `run.bat`（首次先跑 `setup.bat` 装依赖）

程序常驻系统托盘（"译"字图标）。首次运行弹出设置窗口：粘贴[智谱开放平台](https://open.bigmodel.cn)的 API Key，模型默认免费的 GLM-4-Flash。

## 两种翻译方式

**① 翻译放大镜（`Ctrl+Alt+T`）**——浮窗出现在鼠标处，自动翻译它覆盖的区域：

- 拖动/拉伸浮窗，**停稳半秒自动翻译**；浮窗不动、底下内容变化时也会**自动重翻**（托盘"跟随内容变化"开关）
- **悬停对照（默认）**：浮窗实时透视原文（无冻结画面、无模糊），鼠标移到某句上浮现双语气泡，单击该行只复制该行译文
- **替换模式**：双击画布或点工具栏按钮切换，中文按原文位置/字号/配色整页覆盖
- **零闪烁**：截屏不改变窗口状态（画布本身近乎全透明），轮询检测全程无可感知动静
- `↻ 刷新` 手动重翻；`⧉` 复制全部译文；`✕` 关闭

**② 划词翻译（`Ctrl+Alt+B`）**——复制英文文本后按热键，玻璃气泡显示原文/译文对照，可选中复制。

## 托盘菜单

翻译放大镜 / 剪贴板翻译 ｜ 移动后自动翻译 ✓ / 跟随内容变化 ✓ / 开机自启 ✓ ｜ 打开术语表 / 设置… ｜ 退出

## 术语表

项目目录的 `glossary.txt`：每行一条「英文 = 中文」（也支持 `->` 或 Tab），保存**立即生效**，翻译时强制按词表翻译——专有名词全篇一致。托盘"打开术语表"可随时编辑。

## 配置（config.json）

```json
{
  "api_key": "你的智谱 API Key",
  "model": "glm-4-flash",
  "hotkey": "ctrl+alt+t",
  "hotkey_clipboard": "ctrl+alt+b",
  "lens_mode": "hover",
  "auto_translate": true,
  "follow_content": true,
  "poll_interval_ms": 1500,
  "change_threshold": 3.0,
  "invert_threshold": 110
}
```

- `lens_mode`：悬停对照（hover）/ 原位替换（replace）
- `poll_interval_ms` / `change_threshold`：内容检测的轮询间隔与灵敏度（灰度平均差阈值，调小更灵敏）

## 打包 exe

改动代码后双击 `build_exe.bat` 重新打包，产物在 `dist\SnapTrans.exe`。

## 已知限制

- 替换模式的译文贴片会盖住对应原文，被贴片覆盖区域的内容变化检测不到（悬停模式无此问题）
- 译文按 OCR 识别的文本块对位，极小字号或特殊字体的识别误差会影响贴片位置
- 翻译内容会上传给智谱 API，介意敏感信息的话避开即可

## 项目结构

```
snaptrans/
├── app.py             # 主控制器：热键、托盘、自启、划词翻译
├── lens.py            # 翻译放大镜：无闪烁截屏、变化检测、原位替换
├── bubble.py          # 划词翻译气泡
├── ocr_engine.py      # RapidOCR 封装（深底自动反色、按块整理坐标）
├── translator.py      # GLM 翻译客户端（编号协议 + 行级缓存 + 术语表）
├── settings_dialog.py # 设置窗口
├── glass.py           # 玻璃态：亚克力模糊 + QSS
└── config.py          # 配置/术语表路径
launch.py              # exe 打包与开机自启的入口
```
