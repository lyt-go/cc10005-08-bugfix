"""端到端核对脚本：违规启用、屏蔽冲突、合法恢复、并发与版本冲突、路径冻结。

运行：.venv/bin/python scripts/verify_boundary.py
"""
from __future__ import annotations

import threading

from app.services.deepcrawl import DeepcrawlService
from app.services.sitemap import SitemapService
from app.store import store

crawl = DeepcrawlService()
site = SitemapService()

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} :: {detail}")


def find(module: str, entry_id: int) -> dict:
    row = store.find(module, entry_id)
    assert row is not None
    return row


print("== 1. 违规启用：DEEP-0001(草稿) + SITE-0001(草稿) ==")
e, m = crawl.run_action(1, "启用爬取")
check("草稿爬虫不能直接启用", e is None, m)
e, m = crawl.run_action(1, "提交策略评审")
check("站点未识别时不能提交评审", e is None, m)
e, m = crawl.run_action(1, "暂停爬取")
check("草稿爬虫不能暂停", e is None, m)

print("== 2. 合法恢复：SITE-0001 探测/识别 -> DEEP-0001 评审/启用 ==")
e, _ = site.run_action(1, "发起探测")
check("SITE-0001 发起探测", e is not None and e["status"] == "探测中")
e, _ = site.run_action(1, "确认识别")
check("SITE-0001 确认识别 v1", e is not None and e["识别版本"] == 1)
e, m = crawl.run_action(1, "提交策略评审")
check("DEEP-0001 评审成功", e is not None and e["status"] == "策略评审中", m)
e, m = crawl.run_action(1, "提交策略评审")
check("评审中不能重复/回退评审", e is None, m)
e, m = crawl.run_action(1, "启用爬取")
check("DEEP-0001 启用成功", e is not None and e["status"] == "已启用", m)
e, m = crawl.run_action(1, "提交策略评审")
check("已启用不能回退到评审", e is None, m)

print("== 3. 暂停保留路径，重新评审须引用新站点版本 ==")
# 给 DEEP-0001 造两条已完成路径
find("deepcrawl", 1)["已完成路径"] = ["/a/1", "/a/2"]
e, m = crawl.run_action(1, "暂停爬取")
check("暂停成功", e is not None and e["status"] == "已暂停", m)
check("暂停后已完成路径保留", len(find("deepcrawl", 1)["已完成路径"]) == 2)
e, m = crawl.run_action(1, "启用爬取")
check("暂停态不能直接启用（旧路径不被重启激活）", e is None, m)
e, m = crawl.run_action(1, "提交策略评审")
check("同站点版本不能重新评审", e is None, m)

print("== 4. 屏蔽事件记录探测时间与原因 ==")
e, m = site.run_action(1, "标记屏蔽", reason="robots 协议变更，整站禁止采集")
check("标记屏蔽成功", e is not None and e["status"] == "已屏蔽", m)
evt = find("sitemap", 1)["屏蔽事件"]
check("记录屏蔽事件(探测时间+原因)", len(evt) == 1 and evt[0]["屏蔽原因"] and evt[0]["探测时间"], str(evt))
e, m = site.run_action(1, "标记屏蔽", reason="缺原因")
check("无原因不能屏蔽", e is None, m)
# 屏蔽后爬虫启用必须失败
e, m = crawl.run_action(1, "提交策略评审")
check("屏蔽站点下爬虫评审被拒", e is None, m)

print("== 5. 合法恢复：重新探测/识别出新识别版本，再评审/启用 ==")
e, _ = site.run_action(1, "发起探测")
check("已屏蔽可重新发起探测", e is not None and e["status"] == "探测中")
e, _ = site.run_action(1, "确认识别")
check("重新识别得到 v2", e is not None and e["识别版本"] == 2)
check("屏蔽事件在恢复后仍保留", len(find("sitemap", 1)["屏蔽事件"]) == 1)
before = dict(find("deepcrawl", 1))
e, m = crawl.run_action(1, "提交策略评审")
check("基于新版本重新评审成功", e is not None and e["status"] == "策略评审中", m)
d1 = find("deepcrawl", 1)
check("重新评审冻结旧代次路径", len(d1["历史路径代次"]) == 1 and d1["已完成路径"] == [], str(d1["历史路径代次"]))
check("历史代次保留原已完成路径", d1["历史路径代次"][0]["已完成路径"] == ["/a/1", "/a/2"])
e, m = crawl.run_action(1, "启用爬取")
check("恢复后重新启用成功", e is not None and e["status"] == "已启用", m)

print("== 6. DEEP-0003：历史已启用配置的解释与暂停 ==")
d3 = find("deepcrawl", 3)
check("DEEP-0003 初始已启用且绑定已识别 SITE-0003", d3["status"] == "已启用" and d3["评审站点版本"] == 1)
e, m = crawl.run_action(3, "启用爬取")
check("已启用不能重复启用", e is None, m)
e, m = crawl.run_action(3, "暂停爬取")
check("DEEP-0003 可暂停", e is not None and e["status"] == "已暂停", m)
check("DEEP-0003 暂停保留 3 条已完成路径", len(find("deepcrawl", 3)["已完成路径"]) == 3)

print("== 7. 乐观版本冲突 ==")
d3v = find("deepcrawl", 3)["version"]
e, m = crawl.run_action(3, "提交策略评审", expected_version=d3v)  # 站点仍是同版本，会因站点版本未变失败
check("暂停态同版本重新评审被拒", e is None, m)
e, m = crawl.run_action(3, "提交策略评审", expected_version=d3v + 99)
check("过期版本号写入被判冲突并回传冲突版本", e is None and "冲突版本" in m, m)

print("== 8. 并发：站点屏蔽 与 爬虫启用 只允许一个成功（两种先后顺序都成立） ==")


def build_reviewable_fixture(tag: str) -> tuple[int, int]:
    """新建一对相互绑定、处于“站点已识别 + 爬虫评审中(可启用)”的隔离夹具。"""
    s, missing = site.create_entry({
        "站点编号": f"SITE-T{tag}",
        "站点域名": f"t{tag}.example.cn",
        "robots规则": "Disallow: /x/",
    })
    assert not missing and s is not None
    site_id = s["id"]
    site.run_action(site_id, "发起探测")
    site.run_action(site_id, "确认识别")
    c, missing = crawl.create_entry({
        "配置编号": f"DEEP-T{tag}",
        "绑定站点编号": f"SITE-T{tag}",
        "种子URL": f"https://t{tag}.example.cn/",
        "最大深度": "2",
        "路径排除规则": "/x/*",
    })
    assert not missing and c is not None
    crawl_id = c["id"]
    e, m = crawl.run_action(crawl_id, "提交策略评审")
    assert e is not None and e["status"] == "策略评审中", m
    return site_id, crawl_id


# 8a. 屏蔽先于启用：先屏蔽站点，再尝试启用 -> 启用被站点准入拒绝
site_id, crawl_id = build_reviewable_fixture("A")
e, m = site.run_action(site_id, "标记屏蔽", reason="并发测试屏蔽 A")
check("8a 前置：站点屏蔽成功（评审中爬虫不阻止屏蔽）", e is not None and e["status"] == "已屏蔽", m)
e, m = crawl.run_action(crawl_id, "启用爬取")
check("屏蔽先成功后，启用被拒", e is None and ("已屏蔽" in m or "冲突版本" in m), m)
check("最终爬虫未进入已启用", find("deepcrawl", crawl_id)["status"] != "已启用")

# 8b. 启用先于屏蔽：先启用爬虫，再尝试屏蔽 -> 屏蔽被运行中护栏拒绝
site_id, crawl_id = build_reviewable_fixture("B")
e, m = crawl.run_action(crawl_id, "启用爬取")
check("8b 前置：爬虫已启用", e is not None and e["status"] == "已启用", m)
e, m = site.run_action(site_id, "标记屏蔽", reason="运行中尝试屏蔽")
check("运行中爬虫存在时屏蔽被拒并回传冲突版本", e is None and "冲突版本" in m, m)
check("站点保持已识别未被屏蔽", find("sitemap", site_id)["status"] == "已识别")
check("爬虫保持已启用（无半套资格）", find("deepcrawl", crawl_id)["status"] == "已启用")

# 8c. 真正并发：评审态下两个线程同时屏蔽与启用，恰好一个成功
site_id, crawl_id = build_reviewable_fixture("C")
results = {}


def do_block():
    e, m = site.run_action(site_id, "标记屏蔽", reason="并发屏蔽 C")
    results["block"] = (e is not None, m)


def do_enable():
    e, m = crawl.run_action(crawl_id, "启用爬取")
    results["enable"] = (e is not None, m)


t1 = threading.Thread(target=do_block)
t2 = threading.Thread(target=do_enable)
t1.start(); t2.start(); t1.join(); t2.join()
block_ok, block_msg = results["block"]
enable_ok, enable_msg = results["enable"]
print("    block ->", block_ok, block_msg)
print("    enable->", enable_ok, enable_msg)
check("并发下恰好一个成功", block_ok != enable_ok)
loser_msg = block_msg if not block_ok else enable_msg
check("失败方说明冲突版本", "冲突版本" in loser_msg, loser_msg)
final_site = find("sitemap", site_id)["status"]
final_crawl = find("deepcrawl", crawl_id)["status"]
print(f"    最终: SITE-TC={final_site}, DEEP-TC={final_crawl}")
consistent = not (final_site == "已屏蔽" and final_crawl == "已启用")
check("无半套资格：屏蔽与启用不会同时成立", consistent)

print()
print(f"结果：通过 {passed} 项，失败 {failed} 项")
raise SystemExit(1 if failed else 0)
