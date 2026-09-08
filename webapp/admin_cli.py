# -*- coding: utf-8 -*-
"""
管理员命令行工具：人工收款后手动给用户充值 / 查余额 / 查充值记录 / 管理代理。

用法（在项目根目录运行）：
    python -m webapp.admin_cli recharge <用户名> <金额>
    python -m webapp.admin_cli balance <用户名>
    python -m webapp.admin_cli recharges [用户名]
    python -m webapp.admin_cli users          # 列出所有用户
    python -m webapp.admin_cli agent <用户名> [折扣]   # 设为代理，默认 0.8（8 折）
    python -m webapp.admin_cli unagent <用户名>       # 取消代理
    python -m webapp.admin_cli agents         # 列出所有代理
    python -m webapp.admin_cli downline <用户名>      # 查看某用户的下级
    python -m webapp.admin_cli commissions <用户名>   # 查看某代理的返佣明细
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webapp import db
from webapp.referral import COMMISSION_BASE, COMMISSION_RATE, round_money


def _require_user(username: str) -> dict:
    u = db.get_user_by_name(username)
    if not u:
        print(f"错误：用户「{username}」不存在。可用 python -m webapp.admin_cli users 查看。")
        sys.exit(1)
    return u


def cmd_recharge(args: list[str]) -> None:
    if len(args) < 2:
        print("用法：python -m webapp.admin_cli recharge <用户名> <金额>")
        sys.exit(1)
    username, amount_str = args[0], args[1]
    try:
        amount = float(amount_str)
    except ValueError:
        print("错误：金额必须是数字")
        sys.exit(1)
    u = _require_user(username)
    new_balance = db.change_balance(u["id"], amount, f"管理员手动充值 {amount:.1f}")
    print(f"已给「{username}」{'充入' if amount >= 0 else '扣减'} {amount:+.1f} 元，当前余额 {new_balance:.1f} 元")
    # 充值返佣（仅当配置为 recharge 基数时触发）
    if COMMISSION_BASE == "recharge" and amount > 0 and u.get("referrer_id"):
        ref = db.get_user(u["referrer_id"])
        if ref and ref.get("is_agent"):
            commission = round_money(amount * COMMISSION_RATE)
            db.pay_commission(ref["id"], u["id"], commission, "recharge",
                              note=f"下级 {username} 充值返佣")
            print(f"→ 已返佣 {commission:.2f} 元给上级「{ref['username']}」（充值返佣）")


def cmd_balance(args: list[str]) -> None:
    if not args:
        print("用法：python -m webapp.admin_cli balance <用户名>")
        sys.exit(1)
    u = _require_user(args[0])
    print(f"用户：{u['username']} | 余额：{u['balance']:.1f} 元 | 管理员：{'是' if u['is_admin'] else '否'}")


def cmd_recharges(args: list[str]) -> None:
    username = args[0] if args else None
    u = _require_user(username) if username else None
    uid = u["id"] if u else None
    rows = db.list_recharges(uid, limit=30)
    if not rows:
        print("暂无充值记录")
        return
    print(f"{'时间':<20} {'金额':>8}  {'备注'}")
    for r in rows:
        t = __import__("time").strftime("%Y-%m-%d %H:%M", __import__("time").localtime(r["created_at"]))
        print(f"{t:<20} {r['amount']:>+8.1f}  {r['note']}")


def cmd_users(args: list[str]) -> None:
    with __import__("sqlite3").connect(str(db.DB_PATH)) as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute("SELECT id, username, email, balance, is_admin, created_at FROM users ORDER BY id").fetchall()
    if not rows:
        print("暂无用户")
        return
    print(f"{'ID':<4} {'用户名':<16} {'邮箱':<32} {'余额':>8}  {'管理员':<4} {'注册时间'}")
    for r in rows:
        t = __import__("time").strftime("%Y-%m-%d %H:%M", __import__("time").localtime(r["created_at"]))
        email = (r["email"] or "")[:30]
        print(f"{r['id']:<4} {r['username']:<16} {email:<32} {r['balance']:>8.1f}  {'是' if r['is_admin'] else '否':<4} {t}")


def cmd_agent(args: list[str]) -> None:
    if not args:
        print("用法：python -m webapp.admin_cli agent <用户名> [折扣]（默认 0.8 = 8 折）")
        sys.exit(1)
    u = _require_user(args[0])
    discount = float(args[1]) if len(args) > 1 else 0.8
    db.set_agent(u["id"], discount)
    print(f"已将「{u['username']}」设为代理，消费折扣 {discount:.0%}")


def cmd_unagent(args: list[str]) -> None:
    if not args:
        print("用法：python -m webapp.admin_cli unagent <用户名>")
        sys.exit(1)
    u = _require_user(args[0])
    db.unset_agent(u["id"])
    print(f"已取消「{u['username']}」的代理资格")


def cmd_agents(args: list[str]) -> None:
    rows = db.list_agents()
    if not rows:
        print("暂无代理")
        return
    print(f"{'ID':<4} {'用户名':<20} {'余额':>8}  {'折扣':<6} {'设置时间'}")
    for r in rows:
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))
        print(f"{r['id']:<4} {r['username']:<20} {r['balance']:>8.1f}  {r['agent_discount']:.0%}  {t}")


def cmd_downline(args: list[str]) -> None:
    if not args:
        print("用法：python -m webapp.admin_cli downline <用户名>")
        sys.exit(1)
    u = _require_user(args[0])
    rows = db.list_downline(u["id"])
    if not rows:
        print(f"「{u['username']}」暂无下级")
        return
    print(f"「{u['username']}」的下级（{len(rows)} 人）：")
    for r in rows:
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))
        print(f"  {r['id']:<4} {r['username']:<20} 余额 {r['balance']:>7.1f}  注册于 {t}")


def cmd_commissions(args: list[str]) -> None:
    if not args:
        print("用法：python -m webapp.admin_cli commissions <用户名>")
        sys.exit(1)
    u = _require_user(args[0])
    rows = db.list_commissions(u["id"])
    total = db.sum_commission(u["id"])
    print(f"「{u['username']}」累计返佣 {total:.2f} 元（{len(rows)} 条记录）：")
    if not rows:
        return
    kind_names = {"consume": "消费返佣", "recharge": "充值返佣", "register": "拉新奖励"}
    for r in rows:
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))
        print(f"  {t}  +{r['amount']:.2f}  {kind_names.get(r['kind'], r['kind'])}  {r['note']}")


COMMANDS = {
    "recharge": cmd_recharge,
    "balance": cmd_balance,
    "recharges": cmd_recharges,
    "users": cmd_users,
    "agent": cmd_agent,
    "unagent": cmd_unagent,
    "agents": cmd_agents,
    "downline": cmd_downline,
    "commissions": cmd_commissions,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("用法：python -m webapp.admin_cli <命令> [参数...]")
        print("命令：")
        for name in COMMANDS:
            print(f"    {name}")
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
