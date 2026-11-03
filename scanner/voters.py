"""Extra voters that nudge each bet's fair chance up or down, and learn how much to trust themselves.

The scanner already has two voters: the sportsbook consensus and the stats model (a bet must pass both).
These three add information neither of them sees directly:

  line_move    - which way the sportsbook consensus has moved since the first report of the week.
                 Lines that move tend to keep drifting the same way toward kickoff (sharp money).
  injury_news  - ESPN's live injury report and news headlines: questionable players, a starting QB out,
                 a top receiver out (more targets for teammates). Out/doubtful players' props are dropped.
  weather      - wind, rain and cold at kickoff turned into fewer expected points and passing yards.

Each voter returns a nudge to the chance of winning, capped at +/-6 points (and +/-4 for all three together). Its nudge is multiplied by a
trust weight. When bets settle, each voter is scored on whether it pushed the right way, and its weight
rises or falls - so the report shows, week by week, which voters are earning their keep.
"""
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

from .statsmodel import model_ml_prob_away, model_spread_prob, model_team_total_prob, model_total_prob, norm_name
from .teams import FULLNAME_TO_ABBR, norm_abbr

VOTERS = ("line_move", "injury_news", "weather")
DEFAULT_WEIGHTS = {"line_move": 0.5, "injury_news": 0.5, "weather": 0.5}  # start cautious: books already price some of this
NICE = {"line_move": "Line movement", "injury_news": "News/injury", "weather": "Weather"}
MAX_NUDGE = 0.06   # per voter, before trust weight
MAX_TOTAL = 0.04   # all voters together can move a fair chance at most 4 points
W_MIN, W_MAX, W_STEP = 0.25, 1.5, 0.08
PASSING = ("pyd", "recyd", "rec", "ptd")


def _clip(x, lim=MAX_NUDGE):
    return max(-lim, min(lim, x))


# ----------------------------------------------------------------------------- state

def load_state(path):
    try:
        with open(path) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("weights", dict(DEFAULT_WEIGHTS))
    st.setdefault("record", {v: {"right": 0, "n": 0} for v in VOTERS})
    st.setdefault("lines", {})
    for v in VOTERS:
        st["weights"].setdefault(v, DEFAULT_WEIGHTS[v])
        st["record"].setdefault(v, {"right": 0, "n": 0})
    return st


def save_state(path, st):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(st, f, indent=1, default=str)


def record_lines(st, cons, now_utc):
    """Keep a snapshot of each game's consensus every run, so later runs can see how lines moved."""
    for (away, home), c in cons.items():
        key = f"{away}@{home}"
        snap = {"t": now_utc.isoformat(), "p_away_ml": c.get("p_away_ml"), "mu_margin": c.get("mu_margin"),
                "mu_total": c.get("mu_total"), "commence": c.get("commence")}
        st["lines"].setdefault(key, []).append(snap)
    cutoff = now_utc - timedelta(days=3)
    for key in list(st["lines"]):
        snaps = st["lines"][key]
        com = snaps[-1].get("commence")
        try:
            if com and datetime.fromisoformat(com.replace("Z", "+00:00")) < cutoff:
                del st["lines"][key]
                continue
        except ValueError:
            pass
        st["lines"][key] = snaps[-12:]


# ----------------------------------------------------------------------------- ESPN injuries + news

STATUS_RANK = {"out": 3, "injured reserve": 3, "doubtful": 2, "questionable": 1}
NEWS_OUT = ("ruled out", "will not play", "won't play", "inactive", "placed on ir", "out for", "season-ending",
            "out sunday", "out monday", "out thursday", "will miss")
NEWS_QUESTIONABLE = ("questionable", "game-time decision", "limited in practice", "did not practice", "dnp")
NEWS_CLEAR = ("full practice", "expected to play", "will play", "cleared", "activated", "will start",
              "off the injury report", "returns to practice", "no injury designation")


def parse_espn_injuries(j):
    """-> {norm player name: {"status", "team", "pos"}} from ESPN's public injuries feed."""
    out = {}
    for team in (j or {}).get("injuries", []) or []:
        tname = team.get("displayName") or ""
        tab = FULLNAME_TO_ABBR.get(tname) or norm_abbr((team.get("team") or {}).get("abbreviation") or "")
        for inj in team.get("injuries", []) or []:
            ath = inj.get("athlete") or {}
            name = ath.get("displayName") or ath.get("fullName")
            if not name:
                continue
            pos = ((ath.get("position") or {}).get("abbreviation") or "").upper()
            status = (inj.get("status") or (inj.get("type") or {}).get("description") or "").strip()
            out[norm_name(name)] = {"status": status, "team": tab, "pos": pos, "name": name}
    return out


def apply_espn_news(injuries, news_json, now_utc, names):
    """Late headlines can be fresher than the injury report: upgrade/downgrade statuses from them."""
    notes = {}
    for art in (news_json or {}).get("articles", []) or []:
        try:
            pub = datetime.fromisoformat((art.get("published") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if now_utc - pub > timedelta(hours=36):
            continue
        text = f"{art.get('headline', '')}. {art.get('description', '')}".lower()
        for nm, display in names.items():
            if display.lower() not in text:
                continue
            rec = injuries.setdefault(nm, {"status": "", "team": "", "pos": "", "name": display})
            if any(k in text for k in NEWS_OUT):
                rec["status"], rec["news"] = "Out", art.get("headline")
            elif any(k in text for k in NEWS_CLEAR):
                rec["status"], rec["news"] = "", art.get("headline")
            elif any(k in text for k in NEWS_QUESTIONABLE) and not rec.get("status"):
                rec["status"], rec["news"] = "Questionable", art.get("headline")
            notes[nm] = art.get("headline")
    return notes


# ----------------------------------------------------------------------------- the votes

def _side_prob(c, dists, mu_margin, mu_total, p_away_ml):
    """Chance this side wins under a given consensus snapshot (for line-movement comparison)."""
    m, t = c.get("mkt", {}), c["type"]
    if mu_margin is None:
        return None
    if t == "ML":
        pa = p_away_ml if p_away_ml is not None else model_ml_prob_away(dists, mu_margin)
        p_long = pa if m.get("long_team") == c["away"] else 1 - pa
    elif t == "SPREAD":
        p_long = model_spread_prob(dists, mu_margin, m["away_line"])
    elif t == "TOTAL":
        if mu_total is None:
            return None
        p_long = model_total_prob(dists, mu_total, m["line"])
    elif t == "TEAM_TOTAL":
        if mu_total is None:
            return None
        mu_team = (mu_total + mu_margin) / 2 if m.get("team") == c["home"] else (mu_total - mu_margin) / 2
        p_long = model_team_total_prob(dists, mu_team, m["line"])
    else:
        return None
    return p_long if c["side"] == "long" else 1 - p_long


def vote_line_move(c, st, dists):
    snaps = st["lines"].get(f"{c['away']}@{c['home']}") or []
    if len(snaps) < 2:
        return 0.0
    first, last = snaps[0], snaps[-1]
    p0 = _side_prob(c, dists, first.get("mu_margin"), first.get("mu_total"), first.get("p_away_ml"))
    p1 = _side_prob(c, dists, last.get("mu_margin"), last.get("mu_total"), last.get("p_away_ml"))
    if p0 is None or p1 is None:
        return 0.0
    return _clip(0.5 * (p1 - p0))  # half the move again: lines that move tend to keep drifting


def vote_injury(c, injuries, team_qb, players):
    """Returns a nudge, or None to drop the bet (player out/doubtful)."""
    side_sign = 1 if c["side"] == "long" else -1
    v = 0.0
    if c["type"] in ("PROP", "TD") and c.get("player"):
        rec = injuries.get(norm_name(c["player"])) or {}
        rank = STATUS_RANK.get((rec.get("status") or "").lower(), 0)
        if rank >= 2:
            return None
        if rank == 1:
            v -= 0.04 * side_sign  # questionable: real chance of limited snaps
        team = players.team_of.get(norm_name(c["player"])) if players else None
        if team and c.get("kind") in PASSING:
            qb = team_qb.get(team)
            if qb and STATUS_RANK.get((injuries.get(norm_name(qb)) or {}).get("status", "").lower(), 0) >= 2:
                v -= 0.04 * side_sign  # backup QB throwing
        if team and c.get("kind") in ("rec", "recyd"):
            out_receivers = [r for r in injuries.values() if r.get("team") == team and r.get("pos") in ("WR", "TE")
                             and STATUS_RANK.get((r.get("status") or "").lower(), 0) >= 3
                             and norm_name(r.get("name", "")) != norm_name(c["player"])]
            if out_receivers:
                v += 0.02 * side_sign  # more targets to go around
        return _clip(v)
    for team in (c["away"], c["home"]):
        qb = team_qb.get(team)
        if not qb or STATUS_RANK.get((injuries.get(norm_name(qb)) or {}).get("status", "").lower(), 0) < 2:
            continue
        m = c.get("mkt", {})
        if c["type"] == "ML":
            backs_team = (m.get("long_team") == team) == (c["side"] == "long")
            v += -0.03 if backs_team else 0.02
        elif c["type"] == "TEAM_TOTAL" and m.get("team") == team:
            v -= 0.03 * side_sign
        elif c["type"] == "TOTAL":
            v -= 0.02 * side_sign
    return _clip(v)


def vote_weather(c, wx, dists, cons):
    if not wx:
        return 0.0
    temp, wind, rain = wx
    pts = 0.25 * max(0.0, wind - 12) + (1.5 if rain >= 2.0 else 0.0) + (1.0 if temp <= 25 else 0.0)
    if pts <= 0:
        return 0.0
    side_sign = 1 if c["side"] == "long" else -1
    m = c.get("mkt", {})
    if c["type"] in ("TOTAL", "TEAM_TOTAL") and cons and cons.get("mu_total") is not None:
        mu_t, mu_m = cons["mu_total"], cons.get("mu_margin") or 0.0
        if c["type"] == "TOTAL":
            now, adj = model_total_prob(dists, mu_t, m["line"]), model_total_prob(dists, mu_t - pts, m["line"])
        else:
            mu_team = (mu_t + mu_m) / 2 if m.get("team") == c["home"] else (mu_t - mu_m) / 2
            now = model_team_total_prob(dists, mu_team, m["line"])
            adj = model_team_total_prob(dists, mu_team - pts / 2, m["line"])
        return _clip((adj - now) * side_sign)
    if c["type"] == "PROP" and c.get("kind") in PASSING:
        hit = 0.006 * max(0.0, wind - 12) + (0.02 if rain >= 2.0 else 0.0)
        return _clip(-hit * side_sign)
    if c["type"] == "PROP" and c.get("kind") == "ryd" and wind >= 20:
        return _clip(0.01 * side_sign)  # teams lean on the run in big wind
    return 0.0


def apply(cands, st, dists, cons_by_game, injuries, team_qb, players, wx_by_game):
    """Nudge every candidate's fair chance by the weighted votes. Returns the surviving candidates."""
    w = st["weights"]
    out = []
    for c in cands:
        votes = {}
        try:
            votes["line_move"] = vote_line_move(c, st, dists)
        except Exception:
            votes["line_move"] = 0.0
        try:
            iv = vote_injury(c, injuries, team_qb, players)
        except Exception:
            iv = 0.0
        if iv is None:
            continue
        votes["injury_news"] = iv
        try:
            votes["weather"] = vote_weather(c, wx_by_game.get(c["game_slug"]), dists,
                                            cons_by_game.get((c["away"], c["home"])))
        except Exception:
            votes["weather"] = 0.0
        nudge = _clip(sum(w[k] * v for k, v in votes.items()), MAX_TOTAL)
        if nudge:
            c["p_c"] = min(0.99, max(0.01, c["p_c"] + nudge))
            if c.get("p_m") is not None:
                c["p_m"] = min(0.99, max(0.01, c["p_m"] + nudge))
            c["edge_c"] = c["p_c"] - c["cost"]
            c["edge_m"] = None if c.get("p_m") is None else c["p_m"] - c["cost"]
        c["voters"] = {k: round(v, 4) for k, v in votes.items() if abs(v) >= 0.001}
        c["nudge"] = round(nudge, 4)
        out.append(c)
    return out


def learn(st, led):
    """Score each voter on newly settled single bets and adjust its trust weight."""
    learned = 0
    for x in led["bets"]:
        if x.get("kind") != "single" or x.get("result") not in ("win", "loss") or x.get("voters_scored"):
            continue
        won = x["result"] == "win"
        for k, v in (x.get("voters") or {}).items():
            if k not in st["weights"] or abs(v) < 0.005:
                continue
            right = (v > 0) == won
            st["record"][k]["n"] += 1
            st["record"][k]["right"] += int(right)
            st["weights"][k] = min(W_MAX, max(W_MIN, st["weights"][k] * math.exp(W_STEP if right else -W_STEP)))
            learned += 1
        x["voters_scored"] = True
    return learned


def describe(c):
    """'adjusted +1.2 pts: weather -2.0, line movement +0.8' for the report card."""
    parts = [f"{NICE[k]} {100 * v:+.1f}" for k, v in sorted((c.get("voters") or {}).items(), key=lambda kv: -abs(kv[1]))]
    if not parts:
        return ""
    return f"Extra voters moved the fair chance {100 * c.get('nudge', 0):+.1f} pts ({', '.join(parts)} before trust weights)"

