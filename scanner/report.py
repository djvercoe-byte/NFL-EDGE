"""HTML report + short push-notification text."""
import html
from zoneinfo import ZoneInfo

from . import voters as _voters

PT = ZoneInfo("America/Los_Angeles")


def e(s):
    return html.escape(str(s))


def pct(x, d=1):
    return f"{100 * x:.{d}f}%"


def cents(x):
    return f"{100 * x:.1f}¢"


def money(x):
    return ("+" if x >= 0 else "−") + f"${abs(x):.2f}"


def risk_bar(r):
    cls = "lo" if r <= 3 else "mid" if r <= 6 else "hi"
    return (f'<div class="risk"><div class="rbar"><span class="{cls}" style="width:{r * 10}%"></span></div>'
            f'<b>{r}/10</b></div>')


def kickoff_str(dt):
    return dt.astimezone(PT).strftime("%a %-I:%M %p PT")


def pick_card(p, i, moves, liq):
    key = f"{p['slug']}|{p['side']}"
    mv = moves.get(key)
    mv_html = ""
    if mv is not None and abs(mv) >= 0.005:
        good = mv < 0  # price dropped = better for us
        mv_html = (f'<span class="chip {"good" if good else "bad"}">price {"↓" if mv < 0 else "↑"} '
                   f'{abs(100 * mv):.1f}¢ since last report</span>')
    lq = liq.get(key)
    lq_html = ""
    if lq is not None:
        tag = "deep" if lq >= 500 else "ok" if lq >= 75 else "thin"
        lq_html = f'<span class="chip {"good" if tag != "thin" else "bad"}">${lq:,.0f} available at this price ({tag})</span>'
    m = p["info"].get("model") or {}
    model_bits = ""
    if m:
        model_bits = f" · model avg {m.get('mean')} over {m.get('games')} games"
    vtxt = _voters.describe(p)
    notes = "".join(f"<li>{e(n)}</li>" for n in p["notes"] + ([vtxt] if vtxt else []))
    verdict_cls = {"Worth it": "good", "Small edge": "warn"}.get(p["verdict"], "bad")
    return f"""
<article class="card">
  <header><span class="rank">{i}</span><div><h3>{e(p['label'])}</h3>
  <div class="sub">{e(p['game'])} · {kickoff_str(p['kickoff'])} · {e(p['type'].replace('_', ' ').title())}</div></div>
  <span class="verdict {verdict_cls}">{e(p['verdict'])}</span></header>
  <div class="grid">
    <div><label>Buy at</label><b>{cents(p['price'])}</b><small>{p['american']} after fees</small></div>
    <div><label>Fair chance</label><b>{pct(p['p'])}</b><small>books {pct(p['p_c'], 0)} · model {pct(p['p_m'], 0)}</small></div>
    <div><label>Edge</label><b>{pct(p['p'] - p['cost'])}</b><small>breakeven {pct(p['breakeven'])}</small></div>
    <div><label>Exp. profit</label><b class="{'pos' if p['ev10'] > 0 else 'neg'}">{money(p['ev10'])}</b><small>per $10 bet</small></div>
  </div>
  {risk_bar(p['risk'])}
  <div class="chips">{mv_html}{lq_html}<span class="chip">{p['info'].get('books', 0)} sportsbooks{e(model_bits)}</span>
  <span class="chip">paper stake {pct(p['kelly'])} of bankroll</span></div>
  {f'<ul class="notes">{notes}</ul>' if notes else ''}
</article>"""


def parlay_card(pl, i):
    legs = "".join(f"<li>{e(l['label'])} <small>({e(l['game'])}, fair {pct(l['p'], 0)})</small></li>" for l in pl["legs"])
    return f"""
<article class="card">
  <header><span class="rank">P{i}</span><div><h3>{len(pl['legs'])}-leg parlay</h3>
  <div class="sub">Hits {pct(pl['p'])} of the time · fair payout {pl['fair_mult']:.2f}x</div></div></header>
  <ol class="legs">{legs}</ol>
  <div class="grid">
    <div><label>Only bet if app pays</label><b>≥ {pl['min_mult']:.2f}x</b><small>combo price set in-app</small></div>
    <div><label>Legs priced alone</label><b>{pl['est_mult']:.2f}x</b><small>after fees</small></div>
    <div><label>Exp. profit</label><b class="pos">{money(pl['ev10'])}</b><small>per $10 at {pl['est_mult']:.2f}x</small></div>
  </div>
  {risk_bar(pl['risk'])}
</article>"""


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mute:#5d6470;--line:#e3e6eb;--good:#0f7b4a;--goodbg:#e3f5ec;
--bad:#b4232c;--badbg:#fbe7e8;--warn:#8a5a00;--warnbg:#fdf1d8;--acc:#2a56d6}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#eceef2;--mute:#9aa1ad;--line:#262b35;
--good:#4fd18f;--goodbg:#12301f;--bad:#ff7a82;--badbg:#3a1518;--warn:#f2c35b;--warnbg:#33280f;--acc:#7c9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}h1{font-size:22px;margin:4px 0}h2{font-size:17px;margin:26px 0 10px}
.meta{color:var(--mute);font-size:13px}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:14px 0}
.summary div,.card{background:var(--card);border:1px solid var(--line);border-radius:12px}
.summary div{padding:10px}.summary b{display:block;font-size:18px}.summary label,.grid label{color:var(--mute);font-size:12px}
.card{padding:14px;margin:10px 0}.card header{display:flex;gap:10px;align-items:flex-start}
.card h3{margin:0;font-size:16px}.sub{color:var(--mute);font-size:13px}.rank{background:var(--acc);color:#fff;border-radius:8px;
padding:2px 8px;font-weight:700;font-size:13px}.verdict{margin-left:auto;font-size:12px;font-weight:700;padding:3px 8px;border-radius:99px;white-space:nowrap}
.good{color:var(--good);background:var(--goodbg)}.bad{color:var(--bad);background:var(--badbg)}.warn{color:var(--warn);background:var(--warnbg)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0 8px}.grid b{display:block;font-size:17px}
.grid small{color:var(--mute);font-size:11px}.pos{color:var(--good)}.neg{color:var(--bad)}
.risk{display:flex;align-items:center;gap:8px;font-size:13px}.risk b{white-space:nowrap}.rbar{flex:1;height:8px;background:var(--line);border-radius:9px;overflow:hidden}
.rbar span{display:block;height:100%}.rbar .lo{background:var(--good)}.rbar .mid{background:#e0a800}.rbar .hi{background:var(--bad)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.chip{font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--line);color:var(--mute)}
.chip.good,.chip.bad{border:none}.notes{margin:8px 0 0;padding-left:18px;color:var(--mute);font-size:13px}
.legs{margin:10px 0;padding-left:20px}.legs small{color:var(--mute)}table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:6px 4px;border-bottom:1px solid var(--line);text-align:left}details{margin-top:24px;color:var(--mute);font-size:13px}
@media (max-width:560px){.grid,.summary{grid-template-columns:repeat(2,1fr)}}
"""


def voter_table(st):
    if not st:
        return ""
    rows = ""
    for k in _voters.VOTERS:
        rec = st["record"].get(k, {"right": 0, "n": 0})
        hit = pct(rec["right"] / rec["n"], 0) if rec["n"] else "–"
        rows += (f"<tr><td>{e(_voters.NICE[k])}</td><td>{st['weights'].get(k, 0):.2f}x</td>"
                 f"<td>{rec['right']}-{rec['n'] - rec['right']}</td><td>{hit}</td></tr>")
    return ("<h2>Extra voters' track record</h2><div class=\"card\"><div class=\"meta\">Each voter nudges a bet's fair "
            "chance. When bets settle it's scored on whether it pushed the right way, and its trust weight moves "
            "up or down (0.25x-1.5x).</div><table><tr><th>Voter</th><th>Trust</th><th>Right-Wrong</th><th>Hit rate</th></tr>"
            f"{rows}</table></div>")


def render(ctx):
    picks, parlays, sc = ctx["picks"], ctx["parlays"], ctx["scorecard"]
    cards = "".join(pick_card(p, i + 1, ctx["moves"], ctx["liq"]) for i, p in enumerate(picks)) or \
        '<p class="card">No bets cleared the bar this run — the books and the model didn\'t both see value after fees. Sitting out is a win.</p>'
    pcards = "".join(parlay_card(pl, i + 1) for i, pl in enumerate(parlays)) or \
        '<p class="card">No parlays this run (needs 2+ qualifying legs from different games).</p>'
    rows = "".join(f"<tr><td>{e(t.replace('_', ' ').title())}</td><td>{d['w']}-{d['l']}</td><td>{money(d['pnl'])}</td>"
                   f"<td>{pct(d['pnl'] / d['staked']) if d['staked'] else '–'}</td></tr>" for t, d in sorted(sc['by_type'].items()))
    status = "".join(f"<li>{e(s)}</li>" for s in ctx["status"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>NFL Edge Report</title><style>{CSS}</style></head>
<body><main>
<div class="meta">{e(ctx['slot_name'])} · generated {e(ctx['generated'])}</div>
<h1>NFL Polymarket Edge Report — Week {ctx['week']}</h1>
<div class="meta">{ctx['n_games']} games · {ctx['n_markets']:,} Polymarket markets scanned · {ctx['n_priced']:,} sides priced</div>
<div class="summary">
<div><label>Picks</label><b>{len(picks)}</b></div>
<div><label>Parlays</label><b>{len(parlays)}</b></div>
<div><label>Paper record</label><b>{sc['wins']}-{sc['losses']}</b></div>
<div><label>Paper bankroll</label><b>${sc['bankroll']:.2f}</b></div>
</div>
<h2>Single bets</h2>{cards}
<h2>Parlays (different games, this week only)</h2>{pcards}
<h2>Paper-trading scorecard</h2>
<div class="card"><div class="grid">
<div><label>Record</label><b>{sc['wins']}-{sc['losses']}</b><small>{sc['voids']} void · {sc['open']} open</small></div>
<div><label>Profit</label><b class="{'pos' if sc['pnl'] >= 0 else 'neg'}">{money(sc['pnl'])}</b><small>on ${sc['staked']:.2f} staked</small></div>
<div><label>ROI</label><b>{pct(sc['roi'])}</b><small>actual</small></div>
<div><label>Expected</label><b>{money(sc['exp_pnl'])}</b><small>what the edges predicted</small></div>
</div>{f'<table><tr><th>Type</th><th>W-L</th><th>P/L</th><th>ROI</th></tr>{rows}</table>' if rows else '<div class="meta">Grading starts once this week\'s games settle.</div>'}</div>
{voter_table(ctx.get("voters"))}
<details><summary>How to read this</summary>
<p><b>Buy at</b> is the live Polymarket US price for that side; the American odds include Polymarket's taker fee.
<b>Fair chance</b> blends the de-vigged sportsbook consensus (70%) with an independent stats model (30%). A bet only appears when
<i>both</i> say it's underpriced. <b>Exp. profit</b> is the average result per $10 if you made this bet many times — it is not a guarantee.
<b>Risk</b> rises for long shots, props, alt lines far from the sportsbook line, thin markets, injuries, wind/rain, and model disagreement.
Parlay payouts are set in the Polymarket app when you build the combo, so each parlay lists the minimum payout that still makes it worth it.
Nothing here stays open past Monday Night Football.</p>
<p>Paper bankroll started at $100 and stakes are quarter-Kelly, capped at 3% per bet. This is a research tool, not financial advice.</p></details>
<details><summary>Data status</summary><ul>{status}</ul></details>
</main></body></html>"""


def push_text(ctx, url):
    picks, parlays = ctx["picks"], ctx["parlays"]
    if not picks:
        title = f"NFL Edge {ctx['slot_short']}: no bets worth it"
        body = "Nothing cleared the bar after fees this run. Tap for the full scan."
        return title, body
    title = f"NFL Edge {ctx['slot_short']}: {len(picks)} picks, {len(parlays)} parlays"
    lines = []
    for p in picks[:4]:
        lines.append(f"{p['label']} @ {100 * p['price']:.0f}c | edge {100 * (p['p'] - p['cost']):.1f}% | "
                     f"{money(p['ev10'])}/$10 | risk {p['risk']}/10")
    if parlays:
        pl = parlays[0]
        lines.append(f"Parlay: {' + '.join(l['label'] for l in pl['legs'])} (bet if >= {pl['min_mult']:.2f}x)")
    lines.append("Tap for the full report.")
    return title, "\n".join(lines)
