"""配置读写：config.json / glossary.txt 与项目根目录同级（打包成 exe 后在 exe 旁边）。"""

import json
import sys
from pathlib import Path


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller 打包后：配置放在 exe 旁边
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = _app_dir() / "config.json"
GLOSSARY_PATH = _app_dir() / "glossary.txt"

GLOSSARY_TEMPLATE = """# SnapTrans 术语表：每行一条，格式「英文 = 中文」（也支持 -> 或 Tab 分隔）
# 以 # 开头的行为注释；保存本文件后立即生效，无需重启程序。
# 例：
# ZCode = ZCode
# MCP = MCP
# sandbox = 沙箱
# prompt = 提示词
"""

DEFAULTS = {
    "api_key": "",
    "api_base": "https://open.bigmodel.cn/api/paas/v4",
    "model": "glm-4-flash",
    "hotkey": "ctrl+alt+t",
    "hotkey_clipboard": "ctrl+alt+b",
    # 翻译完成后浮窗的显示模式：hover=先显原文，悬停浮现译文；replace=全部原位替换
    "lens_mode": "hover",
    # 移动/调整大小浮窗后自动触发翻译
    "auto_translate": True,
    # 跟随内容变化：浮窗不动、底下内容变化时自动重翻
    "follow_content": True,
    "poll_interval_ms": 1500,
    "change_threshold": 3.0,  # 0~255 灰度平均差超过它才算变化（防止光标闪烁误触发）
    # 灰度均值低于该值视为深色背景，OCR 前自动反色
    "invert_threshold": 110,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass  # 配置损坏时回退默认值
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
