"""站点地图业务规则：单向状态机、版本号与屏蔽事件都收在这里。

站点状态只能沿 草稿 → 探测中 → 已识别 → 已屏蔽 单向推进；已屏蔽如需恢复，
只能重新发起探测（探测中 → 已识别 会生成新的识别版本）。每次状态写入都在
跨模块写锁内完成，与爬虫启用互斥，并通过 version 做乐观并发控制。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.boundary import (
    SITE_ACTION_RULES,
    SITE_ALLOWED,
    SITE_STATUS_ORDER,
    SITEMAP_MODULE,
    ConflictError,
    boundary_lock,
    describe_conflict,
    ensure_expected_version,
    ensure_site_not_running,
)
from app.store import store

REQUIRED_FIELDS = ["站点编号", "站点域名", "robots规则"]


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SitemapService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(SITEMAP_MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("站点编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(SITEMAP_MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(SITEMAP_MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = SITE_STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 站点识别版本：每次确认识别/重新识别递增；屏蔽不递增，只追加屏蔽事件
        entry["version"] = 1
        entry["识别版本"] = 0
        entry["屏蔽原因"] = ""
        entry["屏蔽事件"] = []  # [{探测时间, 屏蔽原因, 站点版本}]
        entry["最近探测时间"] = _now_text()
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        expected_version: int | None = None,
        reason: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        # 整个“校验 + 写入”在跨模块锁内，避免与爬虫启用并发产生双成功
        with boundary_lock:
            entry = store.find(SITEMAP_MODULE, entry_id)
            if entry is None:
                return None, f"站点描述 {entry_id} 不存在或已归档"
            if action not in SITE_ACTION_RULES:
                return None, f"动作「{action}」不属于站点地图可执行范围"

            current_status = str(entry.get("status", ""))
            if action not in SITE_ALLOWED.get(current_status, ()):
                allowed = "、".join(SITE_ALLOWED.get(current_status, ())) or "无可用动作"
                return (
                    None,
                    f"站点「{current_status}」状态下不允许「{action}」，当前仅可执行：{allowed}",
                )

            try:
                ensure_expected_version(entry, expected_version)
            except ConflictError as exc:
                return None, describe_conflict(exc)

            target = SITE_ACTION_RULES[action]
            reason_text = str(reason or "").strip()

            if action == "标记屏蔽":
                if not reason_text:
                    return None, "标记屏蔽必须填写屏蔽原因，用于记录站点屏蔽事件"
                try:
                    ensure_site_not_running(entry)
                except ConflictError as exc:
                    return None, describe_conflict(exc)
                return self._apply_block(entry, reason_text)

            if action == "发起探测":
                # 已屏蔽站点重新探测：清掉屏蔽标记，但屏蔽事件永久保留可追溯
                entry["屏蔽原因"] = ""
                entry["status"] = target
                entry["最近探测时间"] = _now_text()
                entry["pending"] = True
                entry["abnormal"] = True
                return entry, f"站点描述已{action}，原屏蔽事件保留备查"

            if action == "确认识别":
                # 生成新的站点识别版本（首次识别与重新识别都递增）
                new_identify_version = int(entry.get("识别版本", 0)) + 1
                entry["识别版本"] = new_identify_version
                entry["version"] = int(entry.get("version", 1)) + 1
                entry["status"] = target
                entry["最近探测时间"] = _now_text()
                entry["pending"] = False
                entry["abnormal"] = False
                return entry, f"站点描述已{action}，当前识别版本 v{new_identify_version}（数据版本 v{entry['version']}）"

            # 兜底：理论不会走到
            entry["status"] = target
            return entry, f"站点描述已{action}"

    def _apply_block(self, entry: dict[str, Any], reason: str) -> tuple[dict[str, Any], str]:
        probe_time = _now_text()
        event = {
            "探测时间": probe_time,
            "屏蔽原因": reason,
            "站点版本": int(entry.get("识别版本", 0)),
        }
        events = entry.setdefault("屏蔽事件", [])
        events.append(event)
        entry["status"] = "已屏蔽"
        entry["version"] = int(entry.get("version", 1)) + 1
        entry["屏蔽原因"] = reason
        entry["最近探测时间"] = probe_time
        entry["pending"] = False
        entry["abnormal"] = True

        # 屏蔽成功后，给出引用该站点的爬虫当前状态，便于操作者核对连带影响
        affected = [
            str(row.get("配置编号"))
            for row in store.rows("deepcrawl")
            if str(row.get("绑定站点编号", "")).strip() == str(entry.get("站点编号", "")).strip()
        ]
        tail = f"；关联爬虫 {('、'.join(affected))} 的启用资格已随之失效" if affected else ""
        return entry, f"站点描述已标记屏蔽（数据版本 v{entry['version']}）并记录屏蔽事件{tail}"
