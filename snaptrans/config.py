"""配置读写：config.json 与项目根目录同级。"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULTS = {
    "api_key": "",
    "api_base": "https://open.bigmodel.cn/api/paas/v4",
    "model": "glm-4-flash",
    "hotkey": "ctrl+alt+t",
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
