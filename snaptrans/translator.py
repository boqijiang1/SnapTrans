"""GLM 翻译客户端（智谱开放平台 OpenAI 兼容接口）：编号协议 + 行级缓存 + 术语表 + 流式输出。"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

SYSTEM_PROMPT = """你是精准的英译中翻译引擎，擅长软件与 AI 领域的文本。用户发来若干行带编号的英文文本，可能来自软件界面（按钮、菜单），也可能是插件介绍、说明文档等连续文字。请逐行翻译成简体中文：
1. 回复必须同样逐行带相同编号，一行不多、一行不少，不合并、不拆分；
2. 界面按钮、菜单等短语用中文软件惯用译法（Copy→复制、Settings→设置），简短自然；
3. 句子和段落必须准确、通顺、专业，符合中文表达习惯，不要生硬直译，不要漏译信息；
4. 专有名词、产品名、代码、命令、变量名、路径、URL 和占位符（%s、{0} 等）保持原样；AI 领域术语按业界通用译法（prompt→提示词、model→模型、plugin→插件、token→token、context→上下文）；
5. 以 … 结尾的行说明原文在界面上被截断了：先结合其他行判断可见部分最可能的完整含义，再按完整含义翻译，并在译文结尾保留 …；若可见部分无法判断（如孤立字母碎片），保留原文并在结尾加 …，绝不要把截断碎片臆译成无关的中文词；
6. 只输出编号译文行，不要解释、不要空行、不要代码块。"""

_LETTERS = re.compile(r"[A-Za-z]{2}")
_NUM_PREFIX = re.compile(r"^(\d+)\s*[.、)）：:]\s*(.+)$")
_ELLIPSIS = ("。。。", "...", "。。", "…", "⋯")  # 长的先匹配


def _split_truncation(text: str) -> tuple[str, bool]:
    """识别界面截断：剥掉结尾的省略号变体，返回 (净化文本, 是否截断)。"""
    t = text.rstrip()
    for e in _ELLIPSIS:
        if t.endswith(e):
            return t[: -len(e)].rstrip(), True
    return t, False


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
        """非流式逐行翻译（一次性返回）。行数多时分块并行请求。
        界面截断行（结尾 …/...）自动规范化并在译文中保留截断特征。"""
        norm: list[str] = []
        trunc: list[bool] = []
        for t in texts:
            clean, cut = _split_truncation(t)
            norm.append(clean)
            trunc.append(cut)
        unique: list[str] = []
        seen: set[str] = set()
        for t in norm:
            if is_translatable(t) and t not in self._cache and t not in seen:
                seen.add(t)
                unique.append(t)
        chunks = [unique[i : i + self.BATCH] for i in range(0, len(unique), self.BATCH)]
        if len(chunks) > 1:
            with ThreadPoolExecutor(max_workers=min(3, len(chunks))) as pool:
                batch_results = list(pool.map(self._request_batch, chunks))
        else:
            batch_results = [self._request_batch(chunks[0])] if chunks else []
        for chunk, translated in zip(chunks, batch_results):
            for src, dst in zip(chunk, translated):
                self._cache[src] = dst
                if len(self._cache) > self.CACHE_MAX:
                    self._cache.pop(next(iter(self._cache)))
        out: list[str] = []
        for i, t in enumerate(texts):
            if not is_translatable(norm[i]):
                out.append(t)  # 纯符号行原样保留（含省略号本身）
                continue
            dst = self._cache.get(norm[i], norm[i])
            if trunc[i] and dst and not dst.endswith("…"):
                dst += "…"  # 原文被截断：译文必须保留截断特征
            out.append(dst)
        return out

    def translate_lines_streaming(self, texts: list[str], on_line) -> list[str]:
        """流式逐行翻译。on_line(text_index, translated) 会随进度多次调用：
        缓存命中与纯符号行立即回调，其余行在流式响应中每完成一行就回调一次。
        返回完整译文列表（与 translate_lines 一致），并写入缓存。"""
        results: list[str | None] = [None] * len(texts)
        norm: list[str] = []
        trunc: list[bool] = []
        for t in texts:
            clean, cut = _split_truncation(t)
            norm.append(clean)
            trunc.append(cut)

        def _emit(i: int, dst: str):
            if trunc[i] and dst and not dst.endswith("…"):
                dst += "…"  # 截断特征守卫：原文带省略号，译文也必须带
            results[i] = dst
            on_line(i, dst)

        pending: dict[str, list[int]] = {}
        for i, t in enumerate(texts):
            if not is_translatable(norm[i]):
                _emit(i, t)  # 纯符号行原样
            elif norm[i] in self._cache:
                _emit(i, self._cache[norm[i]])
            else:
                pending.setdefault(norm[i], []).append(i)

        unique = list(pending.keys())
        chunks = [unique[i : i + self.BATCH] for i in range(0, len(unique), self.BATCH)]
        for chunk in chunks:
            index_lists = [pending[t] for t in chunk]
            self._request_batch_streaming(chunk, index_lists, results, _emit)

        for i, t in enumerate(texts):
            if results[i] is None:
                _emit(i, self._cache.get(norm[i], norm[i]))
        return results

    def _request_batch_streaming(self, lines: list[str], index_lists: list[list[int]],
                                 results: list, on_line) -> None:
        """流式请求一批编号行，每解析出一行立即回调。结束后做整段解析兜底。"""
        prompt = "\n".join(f"{k + 1}. {t}" for k, t in enumerate(lines))
        url = self.cfg["api_base"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.cfg.get('api_key', '').strip()}"}
        payload = {
            "model": self.cfg.get("model", "glm-4-flash"),
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
            "stream": True,
        }
        last_err = ""
        for _attempt in range(2):
            try:
                with requests.post(url, headers=headers, json=payload,
                                   timeout=(10, 120), stream=True) as resp:
                    if resp.status_code != 200:
                        try:
                            msg = resp.json().get("error", {}).get("message") or resp.text[:300]
                        except ValueError:
                            msg = resp.text[:300]
                        last_err = f"HTTP {resp.status_code}: {msg}"
                        if resp.status_code in (429, 500, 502, 503, 504):
                            time.sleep(1.2)
                            continue
                        raise TranslatorError(last_err)

                    content_parts: list[str] = []
                    emitted: set[int] = set()
                    buf = ""
                    for raw in resp.iter_lines(decode_unicode=True):
                        if not raw or not raw.startswith("data:"):
                            continue
                        data = raw[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                        except (ValueError, KeyError, IndexError):
                            continue
                        content_parts.append(delta)
                        buf += delta
                        while "\n" in buf:
                            done_line, buf = buf.split("\n", 1)
                            self._emit_stream_line(done_line, lines, index_lists, emitted, results, on_line)
                    self._emit_stream_line(buf, lines, index_lists, emitted, results, on_line)

                    # 兜底：流式解析漏掉的行走整段解析补齐
                    missing = [k for k in range(len(lines)) if k not in emitted]
                    if not missing:
                        return
                    try:
                        parsed = _parse_reply("".join(content_parts), len(lines))
                    except TranslatorError:
                        parsed = None
                    if parsed:
                        for k in missing:
                            dst = parsed[k]
                            if not dst:
                                continue
                            emitted.add(k)
                            self._cache[lines[k]] = dst
                            for i in index_lists[k]:
                                results[i] = dst
                                on_line(i, dst)
                        return
                    # 仍然缺失：对缺失部分做一次非流式重试
                    retry_lines = [lines[k] for k in missing]
                    retry_idx = [index_lists[k] for k in missing]
                    for src, idxs, dst in zip(retry_lines, retry_idx, self._request_batch(retry_lines)):
                        self._cache[src] = dst
                        for i in idxs:
                            results[i] = dst
                            on_line(i, dst)
                    return
            except requests.RequestException as exc:
                last_err = f"网络错误: {exc}"
                time.sleep(1.2)
        raise TranslatorError(last_err)

    def _emit_stream_line(self, line: str, lines: list[str], index_lists: list[list[int]],
                          emitted: set[int], results: list, on_line) -> None:
        """解析一行流式输出；是完整的编号译文行就回调并写缓存。"""
        line = line.strip().strip("*").strip()
        if not line:
            return
        m = _NUM_PREFIX.match(line)
        if not m:
            return
        k = int(m.group(1)) - 1
        dst = m.group(2).strip()
        if not (0 <= k < len(lines)) or k in emitted or not dst:
            return
        emitted.add(k)
        self._cache[lines[k]] = dst
        for i in index_lists[k]:
            results[i] = dst
            on_line(i, dst)

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
