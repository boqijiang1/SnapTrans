"""GLM 翻译客户端（智谱开放平台 OpenAI 兼容接口）：编号协议 + 行级缓存 + 术语表。"""

from __future__ import annotations

import os
import re
import time

import requests

SYSTEM_PROMPT = """你是精准的英译中翻译引擎，擅长软件与 AI 领域的文本。用户发来若干行带编号的英文文本，可能来自软件界面（按钮、菜单），也可能是插件介绍、说明文档等连续文字。请逐行翻译成简体中文：
1. 回复同样逐行带相同编号，一行不多、一行不少，不合并、不拆分；
2. 界面按钮、菜单等短语用中文软件惯用译法（Copy→复制、Settings→设置），简短自然；
3. 句子和段落必须准确、通顺、专业，符合中文表达习惯，不要生硬直译，不要漏译信息；
4. 专有名词、产品名、代码、命令、变量名、路径、URL 和占位符（%s、{0} 等）保持原样；AI 领域术语按业界通用译法（prompt→提示词、model→模型、plugin→插件、token→token、context→上下文）；
5. 只输出编号译文行，不要解释、不要空行、不要代码块。"""

_LETTERS = re.compile(r"[A-Za-z]{2}")
_NUM_PREFIX = re.compile(r"^(\d+)\s*[.、)）：:]\s*(.+)$")


class TranslatorError(RuntimeError):
    pass


def is_translatable(text: str) -> bool:
    """没有连续字母的行（纯数字/符号）不值得调 API。"""
    return bool(_LETTERS.search(text))


def _parse_reply(content: str, n: int) -> list[str]:
    """把模型回复按编号解析成 n 行译文；格式异常时抛 TranslatorError。"""
    content = re.sub(r"```[a-zA-Z]*", "", content)
    by_id: dict[int, str] = {}
    plain: list[str] = []
    for raw in content.splitlines():
        line = raw.strip().strip("*").strip()
        if not line:
            continue
        m = _NUM_PREFIX.match(line)
        if m:
            by_id[int(m.group(1))] = m.group(2).strip()
        else:
            plain.append(line)
    if len(by_id) == n:
        return [by_id[i + 1] for i in range(n)]
    if len(plain) == n:
        return plain
    raise TranslatorError(f"模型返回格式异常（期望 {n} 行）：{content[:200]}")


class Translator:
    BATCH = 40
    CACHE_MAX = 800
    GLOSSARY_MAX = 80

    def __init__(self, cfg: dict, glossary_path: str | None = None):
        self.cfg = cfg
        self._cache: dict[str, str] = {}
        self._glossary_path = glossary_path
        self._glossary: list[tuple[str, str]] = []
        self._glossary_mtime: float | None = None

    # ---- 术语表 ----
    def _load_glossary(self) -> None:
        """按文件 mtime 增量加载术语表，保存后即时生效。"""
        path = self._glossary_path
        if not path:
            return
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            if self._glossary:
                self._glossary = []
                self._glossary_mtime = None
            return
        if mtime == self._glossary_mtime:
            return
        terms: list[tuple[str, str]] = []
        try:
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    for sep in ("->", "→", "=", "\t"):
                        if sep in line:
                            src, dst = (part.strip() for part in line.split(sep, 1))
                            if src and dst:
                                terms.append((src, dst))
                            break
        except OSError:
            return
        self._glossary = terms[: self.GLOSSARY_MAX]
        self._glossary_mtime = mtime

    def _system_prompt(self) -> str:
        self._load_glossary()
        if not self._glossary:
            return SYSTEM_PROMPT
        lines = "\n".join(f"{s} = {d}" for s, d in self._glossary)
        return SYSTEM_PROMPT + f"\n\n术语表（遇到下列词语必须严格按此翻译）：\n{lines}"

    def translate_lines(self, texts: list[str]) -> list[str]:
        """逐行翻译，保持行数与顺序；纯符号行原样返回。"""
        unique: list[str] = []
        seen: set[str] = set()
        for t in texts:
            if is_translatable(t) and t not in self._cache and t not in seen:
                seen.add(t)
                unique.append(t)
        for chunk_start in range(0, len(unique), self.BATCH):
            chunk = unique[chunk_start : chunk_start + self.BATCH]
            for src, dst in zip(chunk, self._request_batch(chunk)):
                self._cache[src] = dst
                if len(self._cache) > self.CACHE_MAX:
                    self._cache.pop(next(iter(self._cache)))
        return [t if not is_translatable(t) else self._cache.get(t, t) for t in texts]

    def _request_batch(self, lines: list[str]) -> list[str]:
        prompt = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
        return _parse_reply(self._post(prompt), len(lines))

    def _post(self, prompt: str) -> str:
        url = self.cfg["api_base"].rstrip("/") + "/chat/completions"
        payload = {
            "model": self.cfg.get("model", "glm-4-flash"),
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.cfg.get('api_key', '').strip()}"}
        last_err = ""
        for _attempt in range(2):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=(10, 60))
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
                try:
                    msg = resp.json().get("error", {}).get("message") or resp.text[:300]
                except ValueError:
                    msg = resp.text[:300]
                last_err = f"HTTP {resp.status_code}: {msg}"
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.2)
                    continue
                break
            except requests.RequestException as exc:
                last_err = f"网络错误: {exc}"
                time.sleep(1.2)
        raise TranslatorError(last_err)
