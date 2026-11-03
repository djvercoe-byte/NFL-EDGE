"""Paper-trading ledger: logs every recommended bet and grades it from Polymarket's own settlement."""
import json
import os
from datetime import datetime, timedelta, timezone

START_BANKROLL = 100.0


def load(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"bankroll_start": START_BANKROLL, "bets": []}


def save(path, led):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(led, f, indent=1, default=str)


def bankroll(led):
    b = led.get("bankroll_start", START_BANKROLL)
    for x in led["bets"]:
        if x.get("result") in ("win", "loss", "void"):
            b += x.get("pnl", 0.0)
    return b


def open_keys(led):
    return {x["key"] for x in led["bets"] if x.get("result") is None}


def log_picks(led, picks, parlays, run_key):
    br = bankroll(led)
    existing = {x["key"] for x in led["bets"]}
    added = 0
    for p in picks:
        key = f"{p['slug']}|{p['side']}"
        if key in existing:
            continue
        stake = round(max(1.0, br * p["kelly"]), 2)
        led["bets"].append({
            "key": key, "kind": "single", "run": run_key, "label": p["label"], "game": p["game"],
            "game_slug": p["game_slug"], "slug": p["slug"], "side": p["side"], "type": p["type"],
            "kickoff": p["kickoff"].isoformat(), "cost": round(p["cost"], 4), "p": round(p["p"], 4),
            "stake": stake, "result": None, "voters": p.get("voters") or {},
        })
        added += 1
    for pl in parlays[:3]:
        key = "PARLAY|" + "+".join(sorted(f"{l['slug']}|{l['side']}" for l in pl["legs"]))
        if key in existing:
            continue
        led["bets"].append({
            "key": key, "kind": "parlay", "run": run_key, "label": " + ".join(l["label"] for l in pl["legs"]),
            "legs": [{"game_slug": l["game_slug"], "slug": l["slug"], "side": l["side"]} for l in pl["legs"]],
            "kickoff": max(l["kickoff"] for l in pl["legs"]).isoformat(), "cost": round(pl["cost"], 5),
            "p": round(pl["p"], 5), "stake": 1.0, "result": None,
        })
        added += 1
    return added


def _leg_result(get_event, game_slug, slug, side):
    ev = get_event(game_slug)
    if not ev:
        return None
    for m in ev.get("markets", []):
        if m.get("slug") != slug:
            continue
        status = m.get("status", "")
        if not (m.get("closed") or "RESOLVED" in status or "SETTLED" in status):
            return None
        long_px = None
        for s in m.get("marketSides", []):
            if s.get("long"):
                try:
                    long_px = float(s.get("price"))
                except (TypeError, ValueError):
                    pass
        if long_px is None:
            return None
        if long_px >= 0.99:
            won_long = True
        elif long_px <= 0.01:
            won_long = False
        else:
            return "void"
        return "win" if (won_long == (side == "long")) else "loss"
    return None


def grade(led, get_event, now):
    graded = 0
    for x in led["bets"]:
        if x.get("result") is not None:
            continue
        ko = datetime.fromisoformat(x["kickoff"])
        if now < ko + timedelta(hours=4):
            continue
        if x["kind"] == "single":
            r = _leg_result(get_event, x["game_slug"], x["slug"], x["side"])
        else:
            rs = [_leg_result(get_event, l["game_slug"], l["slug"], l["side"]) for l in x["legs"]]
            if any(r == "loss" for r in rs):
                r = "loss"
            elif all(r in ("win", "void") for r in rs) and rs:
                r = "win" if any(r == "win" for r in rs) else "void"
            else:
                r = None
        if r is None:
            if now > ko + timedelta(days=10):
                x["result"], x["pnl"] = "void", 0.0  # never settled in feed: drop from record
            continue
        x["result"] = r
        if r == "win":
            x["pnl"] = round(x["stake"] * (1 / x["cost"] - 1), 2)
        elif r == "loss":
            x["pnl"] = -x["stake"]
        else:
            x["pnl"] = 0.0
        graded += 1
    return graded


def scorecard(led):
    s = {"wins": 0, "losses": 0, "voids": 0, "staked": 0.0, "pnl": 0.0, "open": 0, "by_type": {},
         "exp_pnl": 0.0}
    for x in led["bets"]:
        if x.get("result") is None:
            s["open"] += 1
            continue
        if x["result"] == "void":
            s["voids"] += 1
            continue
        t = x.get("type", "PARLAY") if x["kind"] == "single" else "PARLAY"
        bt = s["by_type"].setdefault(t, {"w": 0, "l": 0, "pnl": 0.0, "staked": 0.0})
        if x["result"] == "win":
            s["wins"] += 1
            bt["w"] += 1
        else:
            s["losses"] += 1
            bt["l"] += 1
        s["staked"] += x["stake"]
        s["pnl"] += x["pnl"]
        s["exp_pnl"] += x["stake"] * (x["p"] / x["cost"] - 1)
        bt["pnl"] += x["pnl"]
        bt["staked"] += x["stake"]
    s["roi"] = s["pnl"] / s["staked"] if s["staked"] else 0.0
    s["bankroll"] = bankroll(led)
    return s
