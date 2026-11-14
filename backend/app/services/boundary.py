"""站点识别（sitemap）与深度爬取（deepcrawl）之间的准入治理。

这里集中收口两件事：

1. 跨模块的写锁：站点屏蔽与爬虫启用可能并发，所有会改动这两张表的动作都在
   同一把可重入锁内完成，保证“检查 + 写入”原子化，失败即不落地，杜绝半套资格。
2. 爬虫准入口径：启用/评审爬虫时必须引用“当前有效”的站点描述与路径排除规则，
   并通过乐观版本号识别并发冲突，冲突时把对方的冲突版本写进错误说明。

业务约定（单向状态机）：
- 站点：草稿 → 探测中 → 已识别 →（标记屏蔽）→ 已屏蔽；已屏蔽只能重新发起探测。
- 爬虫：草稿 → 策略评审中 → 已启用 → 已暂停；已暂停只能以“新评审代次”重新提交策略评审，
  旧代次已完成路径冻结，不会被重启策略再次激活。
"""
from __future__ import annotations

import threading
from typing import Any

from app.store import store

SITEMAP_MODULE = "sitemap"
DEEPCRAWL_MODULE = "deepcrawl"

# 站点描述状态序列与允许的动作
SITE_STATUS_ORDER = ["草稿", "探测中", "已识别", "已屏蔽"]
SITE_ACTION_RULES: dict[str, str] = {
    "发起探测": "探测中",
    "确认识别": "已识别",
    "标记屏蔽": "已屏蔽",
}

# 爬虫配置状态序列与允许的动作（状态只能沿主链路单向推进）
CRAWL_STATUS_ORDER = ["草稿", "策略评审中", "已启用", "已暂停"]
CRAWL_ACTION_RULES: dict[str, str] = {
    "提交策略评审": "策略评审中",
    "启用爬取": "已启用",
    "暂停爬取": "已暂停",
}

# 哪些源状态可以触发哪个动作；不在表里的组合一律拒绝，杜绝回退与跳级。
SITE_ALLOWED: dict[str, tuple[str, ...]] = {
    "草稿": ("发起探测",),
    "探测中": ("确认识别", "标记屏蔽"),
    "已识别": ("标记屏蔽",),
    "已屏蔽": ("发起探测",),
}
CRAWL_ALLOWED: dict[str, tuple[str, ...]] = {
    "草稿": ("提交策略评审",),
    "策略评审中": ("启用爬取",),
    "已启用": ("暂停爬取",),
    # 暂停后只能重新评审，不能直接启用（旧代次路径不允许被重启激活）
    "已暂停": ("提交策略评审",),
}

# 跨模块写锁：同一进程内串行化 sitemap / deepcrawl 的所有状态写入
boundary_lock = threading.RLock()


class ConflictError(Exception):
    """乐观锁/准入冲突。

    code 用来给前端区分“版本冲突”与普通校验失败；conflict_version 携带对方
    最新版本，便于提示“冲突版本”。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "admission_rejected",
        conflict_version: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.conflict_version = conflict_version


def find_site_by_code(site_code: str) -> dict[str, Any] | None:
    """按站点编号（如 SITE-0001）找到站点描述。"""
    site_code = str(site_code or "").strip()
    if not site_code:
        return None
    for row in store.rows(SITEMAP_MODULE):
        if str(row.get("站点编号", "")).strip() == site_code:
            return row
    return None


def find_referencing_crawls(site_code: str) -> list[dict[str, Any]]:
    """返回引用指定站点的全部爬虫配置。"""
    site_code = str(site_code or "").strip()
    if not site_code:
        return []
    return [
        row
        for row in store.rows(DEEPCRAWL_MODULE)
        if str(row.get("绑定站点编号", "")).strip() == site_code
    ]


def ensure_site_not_running(site: dict[str, Any]) -> None:
    """屏蔽前准入：站点下若存在「已启用」爬虫则拒绝屏蔽并回传冲突版本。

    与 boundary_lock 配合后，无论“屏蔽”与“启用”谁先抢到锁，都只会有一个最终结果
    成功：屏蔽先到则启用被站点准入拒绝；启用先到则屏蔽在此处被拒，永远不会出现
    站点已屏蔽而爬虫仍已启用的半套资格。暂停/评审中的爬虫不阻止屏蔽。
    """
    site_code = str(site.get("站点编号", "")).strip()
    enabled = [c for c in find_referencing_crawls(site_code) if str(c.get("status")) == "已启用"]
    if not enabled:
        return
    crawl = enabled[0]
    conflict_version = int(crawl.get("version", 1))
    codes = "、".join(str(c.get("配置编号")) for c in enabled)
    raise ConflictError(
        f"无法屏蔽站点 {site_code}：关联爬虫 {codes} 当前仍为「已启用」，"
        "请先暂停爬虫再执行屏蔽，避免在已屏蔽站点上保留爬取资格",
        code="site_block_conflict",
        conflict_version=conflict_version,
    )


def ensure_expected_version(entry: dict[str, Any], expected_version: int | None) -> None:
    """乐观版本校验：前端基于旧版本写入时直接判冲突，并回传服务端当前版本。"""
    if expected_version is None:
        return
    current = int(entry.get("version", 1))
    if int(expected_version) != current:
        raise ConflictError(
            f"数据已被其他操作更新：当前版本为 v{current}，提交基于 v{int(expected_version)}，"
            "请重新读取后再操作",
            code="version_conflict",
            conflict_version=current,
        )


def check_site_admission(
    crawl: dict[str, Any],
    *,
    for_review: bool,
) -> dict[str, Any]:
    """爬虫准入检查：返回当前绑定的站点描述；不满足准入时抛 ConflictError。

    任何“启用”动作都必须引用当前有效的站点描述（已识别）和非空的路径排除规则；
    评审动作同样要锁定一个有效站点，否则评审通过的资格没有边界依据。
    """
    site_code = str(crawl.get("绑定站点编号", "")).strip()
    if not site_code:
        raise ConflictError(
            f"爬虫配置 {crawl.get('配置编号')} 未绑定站点描述，无法建立站点准入关系",
        )
    site = find_site_by_code(site_code)
    if site is None:
        raise ConflictError(
            f"绑定站点 {site_code} 不存在或已归档，爬虫准入无有效站点描述可引用",
        )

    # 站点识别版本：每次“确认识别/重新识别”递增，是爬虫引用站点描述的唯一口径；
    # 数据 version 仅用于乐观锁，不参与准入引用。
    site_version = int(site.get("识别版本", 0))
    site_status = str(site.get("status", ""))

    # 草稿 / 探测中 / 已屏蔽 都不是“当前有效”的站点识别结果
    if site_status != "已识别":
        raise ConflictError(
            f"绑定站点 {site_code} 当前为「{site_status}」（识别版本 v{site_version}），"
            "不是有效识别版本；爬虫仅允许引用「已识别」的站点描述",
            conflict_version=site_version,
        )

    # 路径排除规则必须当前有效（非空）
    exclusion = str(crawl.get("路径排除规则", "")).strip()
    if not exclusion:
        raise ConflictError(
            f"爬虫配置 {crawl.get('配置编号')} 缺少有效的路径排除规则，"
            "无法在未定义深度边界的情况下进入评审或启用",
        )

    if for_review:
        # 重新评审必须引用“新的”站点识别版本：识别版本不得早于上一轮评审引用的版本
        last_site_version = crawl.get("评审站点版本")
        if last_site_version is not None and site_version <= int(last_site_version):
            raise ConflictError(
                f"重新评审必须引用新的站点识别版本：当前站点 {site_code} 为 v{site_version}，"
                f"上一轮评审已引用 v{int(last_site_version)}，请先重新探测并确认识别站点",
                conflict_version=site_version,
            )
    else:
        # 启用时引用的站点识别版本/排除规则必须与评审时锁定的快照一致，否则要先重新评审
        reviewed_site_version = crawl.get("评审站点版本")
        if reviewed_site_version is None:
            raise ConflictError(
                f"爬虫配置 {crawl.get('配置编号')} 尚未通过策略评审，不能直接启用",
            )
        if site_version != int(reviewed_site_version):
            raise ConflictError(
                f"站点识别版本已变更：评审锁定 v{int(reviewed_site_version)}，"
                f"当前站点 {site_code} 为 v{site_version}，请基于新版本重新提交策略评审",
                conflict_version=site_version,
            )
        reviewed_exclusion = str(crawl.get("评审路径排除规则", "")).strip()
        if reviewed_exclusion != exclusion:
            raise ConflictError(
                "路径排除规则在评审后发生变化，当前规则与评审锁定快照不一致，请重新提交策略评审",
            )

    return site


def describe_conflict(exc: ConflictError) -> str:
    """统一冲突文案，版本冲突时显式带上冲突版本号。"""
    if exc.code == "version_conflict" and exc.conflict_version is not None:
        return f"{exc.message}（冲突版本：v{exc.conflict_version}）"
    if exc.conflict_version is not None:
        return f"{exc.message}（冲突版本：v{exc.conflict_version}）"
    return exc.message
