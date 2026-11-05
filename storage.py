"""自选股 JSON 持久化。

每个用户一份列表，文件放在宿主数据目录 ``plugins_data/aki_stock/watchlists.json``。
写入使用“同目录临时文件 + ``os.replace``”，避免进程中断留下半个 JSON。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from .models import (
    QuoteUnavailableError,
    StockRef,
    WatchlistDuplicateError,
    WatchlistEntry,
    WatchlistLimitError,
)
from .resolver import clean_display_name, market_for_code, normalize_name


def _clean_code(value: Any) -> str:
    text = str(value if value is not None else "").strip().casefold()
    if text.startswith(("sh", "sz", "bj")):
        text = text[2:]
    return text.zfill(6) if text.isdigit() else text


class WatchlistStore:
    """按用户保存自选股；读改写都在同一把锁内完成。"""

    def __init__(self, path: Path, *, logger: Any | None = None) -> None:
        self._path = Path(path)
        self._logger = logger
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        return self._path

    async def items(self, user_id: int) -> list[WatchlistEntry]:
        async with self._lock:
            data = self._read_locked()
            return self._entries(data, user_id)

    async def find(self, user_id: int, text: str) -> WatchlistEntry | None:
        """按代码或名称在用户已有列表中查找，供删除时避免依赖名称表。"""

        key = normalize_name(text)
        code_key = _clean_code(text)
        for entry in await self.items(user_id):
            if entry.code == code_key or normalize_name(entry.name) == key:
                return entry
        return None

    async def add(self, user_id: int, ref: StockRef, limit: int) -> int:
        async with self._lock:
            data = self._read_locked()
            items = self._entries(data, user_id)
            if any(entry.code == ref.code for entry in items):
                raise WatchlistDuplicateError(f"{ref.code} {ref.name} 已在自选股中。")
            if len(items) >= limit:
                raise WatchlistLimitError(
                    f"自选股最多 {limit} 只，请先删除一只再添加。"
                )
            items.append(
                WatchlistEntry(
                    code=ref.code,
                    name=ref.name,
                    kind=ref.kind,
                    market=ref.market,
                )
            )
            self._set_entries(data, user_id, items)
            self._write_locked(data)
            return len(items)

    async def remove(self, user_id: int, code: str) -> WatchlistEntry | None:
        async with self._lock:
            data = self._read_locked()
            items = self._entries(data, user_id)
            for index, entry in enumerate(items):
                if entry.code != code:
                    continue
                removed = items.pop(index)
                self._set_entries(data, user_id, items)
                self._write_locked(data)
                return removed
            return None

    def _read_locked(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self._path.exists():
            self._data = {"version": 1, "users": {}}
            return self._data
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
            self._data = self._validate(raw)
        except (OSError, ValueError, TypeError) as exc:
            self._warn(f"自选股文件读取失败，已隔离原文件: {type(exc).__name__}: {exc}")
            self._quarantine()
            self._data = {"version": 1, "users": {}}
        return self._data

    def _write_locked(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary = self._path.with_name(self._path.name + ".tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise QuoteUnavailableError("自选股保存失败，请稍后再试。") from exc

    @staticmethod
    def _entries(data: dict[str, Any], user_id: int) -> list[WatchlistEntry]:
        users = data.get("users")
        if not isinstance(users, dict):
            return []
        rows = users.get(str(user_id))
        if not isinstance(rows, list):
            return []
        entries: list[WatchlistEntry] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = _clean_code(row.get("code"))
            name = clean_display_name(str(row.get("name") or ""))
            if not code or not name:
                continue
            kind = row.get("kind") if row.get("kind") in {"stock", "index"} else "stock"
            market = row.get("market") if row.get("market") in {"SH", "SZ", "BJ"} else market_for_code(code)
            entries.append(
                WatchlistEntry(
                    code=code,
                    name=name,
                    kind=kind,  # type: ignore[arg-type]
                    market=market,  # type: ignore[arg-type]
                )
            )
        return entries

    @staticmethod
    def _set_entries(data: dict[str, Any], user_id: int, entries: list[WatchlistEntry]) -> None:
        users = data.setdefault("users", {})
        if not isinstance(users, dict):
            users = {}
            data["users"] = users
        key = str(user_id)
        if entries:
            users[key] = [
                {
                    "code": entry.code,
                    "name": entry.name,
                    "kind": entry.kind,
                    "market": entry.market,
                }
                for entry in entries
            ]
        else:
            users.pop(key, None)

    @staticmethod
    def _validate(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("自选股根节点必须是对象")
        users = raw.get("users")
        if not isinstance(users, dict):
            raise ValueError("自选股 users 必须是对象")
        for key, rows in users.items():
            if not isinstance(rows, list):
                raise ValueError(f"用户 {key} 的自选股必须是数组")
        return {"version": 1, "users": users}

    def _quarantine(self) -> None:
        backup = self._path.with_name(
            f"{self._path.name}.corrupt-{int(time.time())}"
        )
        try:
            os.replace(self._path, backup)
        except OSError as exc:
            self._warn(f"自选股损坏文件隔离失败: {type(exc).__name__}: {exc}")

    def _warn(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.warning(message[:500])
        except Exception:  # pragma: no cover - 日志失败不影响业务
            pass
