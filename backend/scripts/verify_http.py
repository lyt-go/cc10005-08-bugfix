"""HTTP 端到端核对：通过真实 uvicorn 接口验证 ok:false 语义、版本、屏蔽原因与重读一致性。

前置：后端已在 127.0.0.1:8000 运行。
运行：.venv/bin/python scripts/verify_http.py
"""
from __future__ import annotations

import json
import threading
import urllib.request

BASE = "http://127.0.0.1:8000"

passed = 0
failed = 0


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} :: {detail}")


def action(module: str, entry_id: int, payload: dict) -> dict:
    return call("POST", f"/api/{module}/{entry_id}/actions", {"values": payload})


def get_entry(module: str, entry_id: int) -> dict:
    return call("GET", f"/api/{module}/{entry_id}")


print("== HTTP 1. 违规启用返回 HTTP 200 + ok:false（前端据此提示而非静默刷新） ==")
r = action("deepcrawl", 1, {"action": "启用爬取"})
check("草稿直接启用被业务拒绝", r["ok"] is False, str(r))
check("拒绝信息可读", "不允许" in r["message"] or "准入" in r["message"] or "站点" in r["message"], r["message"])
r = action("sitemap", 2, {"action": "标记屏蔽", "reason": ""})
check("屏蔽缺原因被拒（SITE-0002 探测中可屏蔽）", r["ok"] is False and "屏蔽原因" in r["message"], r["message"])

print("== HTTP 2. 合法恢复链路 SITE-0001 + DEEP-0001 ==")
check("SITE-0001 发起探测", action("sitemap", 1, {"action": "发起探测"})["ok"])
r = action("sitemap", 1, {"action": "确认识别"})
check("SITE-0001 识别", r["ok"] and r["entry"]["识别版本"] == 1, str(r))
r = action("deepcrawl", 1, {"action": "提交策略评审"})
check("DEEP-0001 评审", r["ok"] and r["entry"]["status"] == "策略评审中", str(r))
reviewed_ver = r["entry"]["version"]
r = action("deepcrawl", 1, {"action": "启用爬取"})
check("DEEP-0001 启用", r["ok"] and r["entry"]["status"] == "已启用", str(r))
# 过期版本号写入 -> 冲突
r = action("deepcrawl", 1, {"action": "暂停爬取", "version": reviewed_ver - 100})
check("过期版本写入判冲突并回传冲突版本", (not r["ok"]) and "冲突版本" in r["message"], r["message"])
r = action("deepcrawl", 1, {"action": "暂停爬取"})
check("不带版本可正常暂停", r["ok"] and r["entry"]["status"] == "已暂停", str(r))

print("== HTTP 3. 重读持久一致：列表与详情版本/状态对得上 ==")
lst = call("GET", "/api/deepcrawl?keyword=DEEP-0001")
list_row = next(x for x in lst["items"] if x["配置编号"] == "DEEP-0001")
detail = get_entry("deepcrawl", 1)
check("列表与详情状态一致", list_row["status"] == detail["status"] == "已暂停", f"{list_row['status']} vs {detail['status']}")
check("列表与详情版本一致", list_row["version"] == detail["version"], f"{list_row['version']} vs {detail['version']}")
check("详情带准入状态视图", "准入状态" in detail, str(detail.keys()))
check("重读保留原版本号", isinstance(detail["version"], int) and detail["version"] >= 1, str(detail["version"]))

print("== HTTP 4. 屏蔽事件 + 恢复后事件保留 + 重新评审冻结路径 ==")
# 先造两条已完成路径
d1 = get_entry("deepcrawl", 1)
r = action("sitemap", 1, {"action": "标记屏蔽", "reason": "robots 协议变更", "version": get_entry("sitemap", 1)["version"]})
check("带原因屏蔽成功", r["ok"] and r["entry"]["status"] == "已屏蔽", str(r))
s1 = get_entry("sitemap", 1)
check("屏蔽事件含探测时间与原因", len(s1["屏蔽事件"]) == 1 and s1["屏蔽事件"][0]["屏蔽原因"] == "robots 协议变更", str(s1["屏蔽事件"]))
check("屏蔽后爬虫启用被拒", action("deepcrawl", 1, {"action": "提交策略评审"})["ok"] is False)
action("sitemap", 1, {"action": "发起探测"})
r = action("sitemap", 1, {"action": "确认识别"})
check("重新识别升版到 v2", r["ok"] and r["entry"]["识别版本"] == 2, str(r))
s1b = get_entry("sitemap", 1)
check("恢复后屏蔽事件仍保留", len(s1b["屏蔽事件"]) == 1, str(s1b["屏蔽事件"]))
r = action("deepcrawl", 1, {"action": "提交策略评审"})
check("新版本重新评审成功", r["ok"] and r["entry"]["status"] == "策略评审中", str(r))
r = action("deepcrawl", 1, {"action": "启用爬取"})
check("恢复后启用成功", r["ok"] and r["entry"]["status"] == "已启用", str(r))

print("== HTTP 5. DEEP-0003 历史已启用配置：暂停保留路径，旧路径不被重启激活 ==")
d3 = get_entry("deepcrawl", 3)
check("DEEP-0003 初始已启用绑定已识别 SITE-0003", d3["status"] == "已启用" and d3["绑定站点编号"] == "SITE-0003", str(d3["status"]))
r = action("deepcrawl", 3, {"action": "暂停爬取"})
check("DEEP-0003 暂停", r["ok"], str(r))
check("暂停保留 3 条已完成路径", len(get_entry("deepcrawl", 3)["已完成路径"]) == 3)
check("暂停态直接启用被拒", action("deepcrawl", 3, {"action": "启用爬取"})["ok"] is False)

print("== HTTP 6. SITE-0002 探测中：DEEP-0002 无法获得资格（屏蔽/未识别冲突） ==")
s2 = get_entry("sitemap", 2)
check("SITE-0002 初始探测中（非已识别）", s2["status"] == "探测中", str(s2["status"]))
r = action("deepcrawl", 2, {"action": "启用爬取"})
check("DEEP-0002 评审中无法跳级启用", r["ok"] is False, r["message"])
# SITE-0002 屏蔽（探测中允许屏蔽）后，DEEP-0002 评审也被准入拒绝
r = action("sitemap", 2, {"action": "标记屏蔽", "reason": "目标域拒绝抓取"})
check("探测中可标记屏蔽", r["ok"], str(r))
# DEEP-0002 当前策略评审中，无启用按钮可点（状态机），且其准入视图显示阻断
d2 = get_entry("deepcrawl", 2)
check("DEEP-0002 准入视图判定不可启用", d2["准入状态"]["可启用"] is False, str(d2["准入状态"]))

print("== HTTP 7. 并发：屏蔽与启用经 HTTP 同时打到服务端，恰好一个成功 ==")
# 屏蔽侧护栏：DEEP-0001 当前已启用，屏蔽其站点必须被拒并回传冲突版本
guard = action("sitemap", 1, {"action": "标记屏蔽", "reason": "运行中尝试屏蔽"})
check("爬虫已启用时屏蔽被拒并回传冲突版本", (not guard["ok"]) and "已启用" in guard["message"] and "冲突版本" in guard["message"], guard["message"])


def build_reviewable_fixture(tag: str) -> tuple[int, int]:
    """经 HTTP 新建一对“站点已识别 + 爬虫评审中(可启用)”的隔离夹具，返回 (site_id, crawl_id)。"""
    r = call("POST", "/api/sitemap", {"values": {
        "站点编号": f"SITE-H{tag}", "站点域名": f"h{tag}.example.cn", "robots规则": "Disallow: /x/"}})
    site_id = r["entry"]["id"]
    action("sitemap", site_id, {"action": "发起探测"})
    action("sitemap", site_id, {"action": "确认识别"})
    r = call("POST", "/api/deepcrawl", {"values": {
        "配置编号": f"DEEP-H{tag}", "绑定站点编号": f"SITE-H{tag}",
        "种子URL": f"https://h{tag}.example.cn/", "最大深度": "2", "路径排除规则": "/x/*"}})
    crawl_id = r["entry"]["id"]
    rv = action("deepcrawl", crawl_id, {"action": "提交策略评审"})
    assert rv["ok"], rv
    return site_id, crawl_id


# 启用先于屏蔽的确定性校验
site_id, crawl_id = build_reviewable_fixture("B")
r = action("deepcrawl", crawl_id, {"action": "启用爬取"})
check("前置：隔离爬虫已启用", r["ok"] and r["entry"]["status"] == "已启用", str(r))
r = action("sitemap", site_id, {"action": "标记屏蔽", "reason": "运行中屏蔽"})
check("运行中爬虫屏蔽被拒并回传冲突版本", (not r["ok"]) and "冲突版本" in r["message"], r["message"])

# 真正并发：评审态下两个线程同时屏蔽与启用
site_id, crawl_id = build_reviewable_fixture("C")
results: dict[str, dict] = {}


def fire_block() -> None:
    results["block"] = action("sitemap", site_id, {"action": "标记屏蔽", "reason": "并发屏蔽"})


def fire_enable() -> None:
    results["enable"] = action("deepcrawl", crawl_id, {"action": "启用爬取"})


t1 = threading.Thread(target=fire_block)
t2 = threading.Thread(target=fire_enable)
t1.start(); t2.start(); t1.join(); t2.join()
print("    block ->", results["block"]["ok"], results["block"]["message"])
print("    enable->", results["enable"]["ok"], results["enable"]["message"])
check("并发下恰好一个成功", results["block"]["ok"] != results["enable"]["ok"])
final_site = get_entry("sitemap", site_id)["status"]
final_crawl = get_entry("deepcrawl", crawl_id)["status"]
check("屏蔽与启用不同时成立", not (final_site == "已屏蔽" and final_crawl == "已启用"), f"{final_site}/{final_crawl}")
loser_msg = results["block"]["message"] if not results["block"]["ok"] else results["enable"]["message"]
check("失败方携带冲突版本说明", "冲突版本" in loser_msg, loser_msg)

print()
print(f"HTTP 结果：通过 {passed} 项，失败 {failed} 项")
raise SystemExit(1 if failed else 0)
