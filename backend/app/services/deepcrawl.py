"""深度爬取业务规则：单向状态机、站点准入、评审快照与路径代次管理。

修复要点：
- 状态只能沿 草稿 → 策略评审中 → 已启用 → 已暂停 单向推进，禁止回退；
  已暂停只能以“新评审代次”重新提交策略评审，不能直接启用。
- 任何启用/评审动作都必须引用当前有效的站点描述（已识别）与非空路径排除规则，
  并校验评审时锁定的站点版本、排除规则快照未漂移。
- 暂停保留已完成路径；重新评审开启新一代次，旧代次路径冻结进历史，重启策略
  无法再次激活旧路径。
- 所有写入与站点屏蔽共用一把跨模块锁，配合 version 乐观锁，保证并发下只有一个
  最终结果成功，失败方拿到冲突版本且不产生半套爬取资格。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.boundary import (
    CRAWL_ACTION_RULES,
    CRAWL_ALLOWED,
    CRAWL_STATUS_ORDER,
    DEEPCRAWL_MODULE,
    ConflictError,
    boundary_lock,
    check_site_admission,
    describe_conflict,
    ensure_expected_version,
    find_site_by_code,
)
from app.store import store

REQUIRED_FIELDS = ["配置编号", "种子URL", "最大深度", "绑定站点编号", "路径排除规则"]


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DeepcrawlService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(DEEPCRAWL_MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("配置编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(DEEPCRAWL_MODULE, entry_id)
        if entry is None:
            return None
        # 详情读取时附带当前准入结论，供前端展示，不修改存储数据
        result = dict(entry)
        result["准入状态"] = self._admission_view(entry)
        return result

    def _admission_view(self, crawl: dict[str, Any]) -> dict[str, Any]:
        """只读的准入判定：与写入时的检查口径保持一致，用于列表/详情展示。"""
        site_code = str(crawl.get("绑定站点编号", "")).strip()
        site = find_site_by_code(site_code) if site_code else None
        view: dict[str, Any] = {
            "绑定站点编号": site_code,
            "站点存在": site is not None,
            "站点状态": site.get("status") if site else None,
            "站点识别版本": site.get("识别版本") if site else None,
            "路径排除规则有效": bool(str(crawl.get("路径排除规则", "")).strip()),
            "可启用": False,
            "可评审": False,
            "阻断原因": "",
        }
        if site is None:
            view["阻断原因"] = "绑定站点不存在或未配置"
            return view

        # 评审准入
        review_admission_ok = False
        try:
            check_site_admission(crawl, for_review=True)
            review_admission_ok = True
        except ConflictError as exc:
            view["评审阻断原因"] = exc.message

        # 启用准入（仅在已评审且快照一致时成立）
        enable_admission_ok = False
        try:
            check_site_admission(crawl, for_review=False)
            enable_admission_ok = True
        except ConflictError as exc:
            view["阻断原因"] = exc.message

        # 最终可执行性 = 状态机允许 且 站点准入通过，与前端按钮置灰口径完全一致
        current_status = str(crawl.get("status", ""))
        view["可评审"] = review_admission_ok and "提交策略评审" in CRAWL_ALLOWED.get(current_status, ())
        view["可启用"] = enable_admission_ok and "启用爬取" in CRAWL_ALLOWED.get(current_status, ())
        return view

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(DEEPCRAWL_MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 登记阶段即建立与站点描述的绑定关系
        entry["status"] = CRAWL_STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["version"] = 1
        entry["评审代次"] = 0
        entry["评审站点版本"] = None
        entry["评审路径排除规则"] = ""
        entry["已完成路径"] = []
        entry["历史路径代次"] = []  # 暂停后重新评审时冻结的旧代次路径
        entry["暂停时间"] = ""
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        expected_version: int | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        # 与站点屏蔽共用同一把锁：检查 + 写入原子完成，失败不落地任何半套资格
        with boundary_lock:
            entry = store.find(DEEPCRAWL_MODULE, entry_id)
            if entry is None:
                return None, f"爬虫配置 {entry_id} 不存在或已归档"
            if action not in CRAWL_ACTION_RULES:
                return None, f"动作「{action}」不属于深度爬取可执行范围"

            current_status = str(entry.get("status", ""))
            if action not in CRAWL_ALLOWED.get(current_status, ()):
                allowed = "、".join(CRAWL_ALLOWED.get(current_status, ())) or "无可用动作"
                return (
                    None,
                    f"爬虫「{current_status}」状态下不允许「{action}」，当前仅可执行：{allowed}",
                )

            try:
                ensure_expected_version(entry, expected_version)
            except ConflictError as exc:
                return None, describe_conflict(exc)

            try:
                if action == "提交策略评审":
                    return self._submit_review(entry)
                if action == "启用爬取":
                    return self._enable(entry)
                if action == "暂停爬取":
                    return self._pause(entry)
            except ConflictError as exc:
                # 准入/版本冲突：不修改任何字段，直接回传带冲突版本的说明
                return None, describe_conflict(exc)

            return None, f"动作「{action}」未被处理"

    def _submit_review(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
        current_status = str(entry.get("status", ""))
        re_review = current_status == "已暂停"
        # 评审也要引用当前有效站点描述与路径排除规则；重新评审必须基于新站点版本
        site = check_site_admission(entry, for_review=True)

        if re_review:
            # 冻结上一代已完成路径：保留可追溯，但新代次不会再激活它们
            done_paths = list(entry.get("已完成路径", []))
            generation = int(entry.get("评审代次", 0))
            if done_paths or generation > 0:
                history = entry.setdefault("历史路径代次", [])
                history.append({
                    "评审代次": generation,
                    "冻结时间": _now_text(),
                    "已完成路径": done_paths,
                    "原站点识别版本": entry.get("评审站点版本"),
                })
            entry["已完成路径"] = []
            entry["暂停时间"] = ""

        # 锁定本轮评审引用的站点识别版本与路径排除规则快照
        entry["评审站点版本"] = int(site.get("识别版本", 0))
        entry["评审路径排除规则"] = str(entry.get("路径排除规则", "")).strip()
        entry["评审代次"] = int(entry.get("评审代次", 0)) + 1
        entry["status"] = "策略评审中"
        entry["version"] = int(entry.get("version", 1)) + 1
        entry["pending"] = True
        entry["abnormal"] = False
        label = "重新提交策略评审" if re_review else "提交策略评审"
        return (
            entry,
            f"爬虫配置已{label}（第 {entry['评审代次']} 代），引用站点 "
            f"{site.get('站点编号')} 识别版本 v{entry['评审站点版本']}，数据版本 v{entry['version']}",
        )

    def _enable(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
        # 启用必须引用当前有效站点描述，且站点版本/排除规则与评审快照完全一致
        site = check_site_admission(entry, for_review=False)
        entry["status"] = "已启用"
        entry["version"] = int(entry.get("version", 1)) + 1
        entry["pending"] = False
        entry["abnormal"] = False
        return (
            entry,
            f"爬虫配置已启用爬取，引用站点 {site.get('站点编号')} 识别版本 "
            f"v{int(site.get('识别版本', 0))} 与当前路径排除规则，数据版本 v{entry['version']}",
        )

    def _pause(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
        # 暂停不丢弃已完成路径，只记录暂停时间并退出启用态
        entry["status"] = "已暂停"
        entry["version"] = int(entry.get("version", 1)) + 1
        entry["暂停时间"] = _now_text()
        entry["pending"] = False
        entry["abnormal"] = False
        kept = len(entry.get("已完成路径", []))
        return entry, f"爬虫配置已暂停爬取，已完成路径（{kept} 条）原样保留，数据版本 v{entry['version']}"
