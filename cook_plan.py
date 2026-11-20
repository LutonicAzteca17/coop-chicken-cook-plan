"""
Coop Chicken - hourly cook plan.

Reads the weekly Toast export (hourly pieces sold) and the waste log, and writes
index.html: a fry-station screen that says how many pieces to drop each hour,
for each location and day of the week.

Run it each week on the new export:
    python3 cook_plan.py                 # uses ./data
    python3 cook_plan.py path/to/export  # folder holding toast_hourly_sales.csv

Only the Python standard library is needed.
"""

import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# ---- Settings a manager might change -------------------------------------
WEEKS_OF_HISTORY = 6     # how many recent weeks the forecast averages
BUFFER = 0.10            # cook 10% above the forecast (tested with 12-min fry time, 90-min hold and the backup rule: least waste for the same wait)
ROUND_TO = 4             # drop in multiples of 4 pieces
MIN_DROP = 4             # never drop fewer than this in a cook hour
MIN_EXTRA = 4            # ...but always at least 4 pieces above the forecast (protects slow afternoon hours)
STOCKOUT_UPLIFT = 1.15   # ASSUMPTION: when the warmer ran empty, plan 15% above what sold there.
                         # Toast can't see walk-aways; the in-store tally will confirm or change this.
COST_PER_PIECE = 1.10    # what one raw piece costs (from Dre)
CURRENT_BATCH = 24       # today's fixed batch, used for the before/after comparison
BACKTEST_WEEKS = 6       # weeks replayed to test the plan (same 6-week window the forecast uses)
HOLD_MINUTES = 90        # Coop's rule: chicken is tossed after 90 minutes under the heat lamp
FRY_MINUTES = 12         # a batch takes 12 minutes, so each drop starts 12 minutes before its hour
BACKUP_AT = 6            # "down to 4 pieces? drop 4 more now"
BACKUP_DROP = 4          # "running out before the next drop? drop 4 more"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def hour_key(text):
    """'9:00', '09:00:00', '9', '9 PM' -> '09:00' / '21:00' (so hours sort correctly)."""
    t = text.strip().upper()
    pm, am = t.endswith("PM"), t.endswith("AM")
    h = int(t.replace("AM", "").replace("PM", "").strip().split(":")[0])
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    return f"{h:02d}:00"


def read_csv(path, needed):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    missing = [c for c in needed if rows and c not in rows[0]]
    if not rows or missing:
        sys.exit(f"Problem with {path.name}: " +
                 (f"missing column(s) {', '.join(missing)}" if rows else "file is empty") +
                 f". Expected columns: {', '.join(needed)}")
    return rows


def load(folder):
    folder = Path(folder)
    src = folder / "toast_hourly_sales.csv"
    if not src.exists():
        sys.exit(f"Can't find {src}. Put the Toast export in that folder with that name.")
    sales = []
    for r in read_csv(src, ["date", "location", "hour", "fried_chicken_pieces_sold"]):
        if not r["fried_chicken_pieces_sold"].strip():
            continue
        sales.append((date.fromisoformat(r["date"].strip()), r["location"].strip(),
                      hour_key(r["hour"]), int(float(r["fried_chicken_pieces_sold"]))))
    cook_hours = []
    sched = folder / "current_cook_schedule.csv"
    if sched.exists():
        cook_hours = sorted({hour_key(r["cook_time"]) for r in read_csv(sched, ["cook_time"])})
    lbs_per_piece = 0.42
    waste = folder / "waste_log.csv"
    if waste.exists():
        rows = read_csv(waste, ["pieces_discarded", "lbs_discarded"])
        pcs = sum(int(r["pieces_discarded"]) for r in rows)
        lbs = sum(float(r["lbs_discarded"]) for r in rows)
        if pcs:
            lbs_per_piece = lbs / pcs
    return sales, cook_hours, lbs_per_piece


def load_plans_used(folder):
    """Every run saves the plan it made as plans/plan_week_of_YYYY-MM-DD.csv.
    Returns {(week_start, location, weekday, hour): pieces}."""
    used = {}
    for f in sorted((Path(__file__).parent / "plans").glob("plan_week_of_*.csv")):
        start = date.fromisoformat(f.stem.replace("plan_week_of_", ""))
        for r in read_csv(f, ["location", "day", "hour", "pieces"]):
            used[(start, r["location"], DAYS.index(r["day"]), hour_key(r["hour"]))] = int(r["pieces"])
    return used


def cooked_that_day(d, loc, cook_hours, used):
    """What the kitchen was told to cook on date d: the saved plan for that week if
    there is one, otherwise the old fixed batch."""
    start = d - timedelta(days=d.weekday())
    if any(k[0] == start for k in used):
        return {h: used.get((start, loc, d.weekday(), h), CURRENT_BATCH) for h in cook_hours}
    return {h: CURRENT_BATCH for h in cook_hours}


def by_day(sales):
    """{(date, location): [(hour, sold), ...]} in hour order."""
    days = defaultdict(list)
    for d, loc, h, q in sales:
        days[(d, loc)].append((h, q))
    for k in days:
        days[k].sort()
    return days


def simulate(hours_sold, plan, cook_hours, backup=0, hold=None, lead=None, backup_at=None):
    """Replay one day of real Toast sales minute by minute (each hour's sales spread
    evenly). Each drop goes into the fryer `lead` minutes before its hour and is ready
    FRY_MINUTES later. Oldest chicken is sold first, and anything older than `hold`
    minutes is tossed. With backup > 0: when the warmer is down to `backup_at` pieces
    (or fewer), nothing is frying and the next drop is more than FRY_MINUTES away,
    the cook drops `backup` more.
    Returns (pieces thrown out, minutes customers spent waiting for chicken (one
    piece waiting one minute = 1), extra pieces dropped, pieces customers had to
    wait for)."""
    lead = FRY_MINUTES if lead is None else lead
    backup_at = BACKUP_AT if backup_at is None else backup_at
    sales = defaultdict(int)
    for h, q in hours_sold:
        base = int(h[:2]) * 60
        for i in range(q):
            sales[base + int((i + 0.5) * 60 / q)] += 1
    starts = {int(h[:2]) * 60 - lead: plan.get(h, 0) for h in cook_hours if plan.get(h, 0)}
    ready_times = sorted(t + FRY_MINUTES for t in starts)
    first_open = min(int(h[:2]) * 60 for h in cook_hours)
    frying, warm, tossed, waited, extra, queue, waited_pcs = [], [], 0, 0, 0, 0, 0
    end = max(list(sales) + ready_times) + 1
    for m in range(min(starts) if starts else 0, end + 1):
        if m in starts:
            frying.append([m + FRY_MINUTES, starts[m]])
        for f in [f for f in frying if f[0] == m]:
            warm.append([m, f[1]]); frying.remove(f)
        if hold:
            for w in warm:
                if m - w[0] >= hold:
                    tossed += w[1]; w[1] = 0
            warm = [w for w in warm if w[1] > 0]
        nxt = [r for r in ready_times if r > m]
        if (backup and m >= first_open and nxt and not frying
                and sum(w[1] for w in warm) <= backup_at and nxt[0] - m > FRY_MINUTES):
            frying.append([m + FRY_MINUTES, backup]); extra += backup
        # Toast recorded these sales, so customers who find the warmer empty wait
        # for the next batch rather than leave. They're counted as "had to wait".
        queue += sales.get(m, 0)
        while queue and warm:
            take = min(queue, warm[0][1])
            warm[0][1] -= take; queue -= take
            if warm[0][1] == 0:
                warm.pop(0)
        waited += queue
        waited_pcs += min(queue, sales.get(m, 0))   # this minute's customers still unserved
    tossed += sum(w[1] for w in warm) + sum(f[1] for f in frying)
    return tossed, waited, extra, waited_pcs


def estimate_demand(days, cook_hours, used):
    """Toast only records what was sold. When the warmer ran empty, the
    real demand was higher, so those hours are scaled up."""
    demand = {}
    for key, hours in days.items():
        cooked = cooked_that_day(key[0], key[1], cook_hours, used)
        on_hand, est = 0, []
        for h, q in hours:
            on_hand += cooked.get(h, 0)
            on_hand -= q
            ran_out = on_hand <= 0 and h in cook_hours
            est.append((h, math.ceil(q * STOCKOUT_UPLIFT) if ran_out else q))
        demand[key] = est
    return demand


def build_plan(demand, cook_hours, until=None):
    """Average the last WEEKS_OF_HISTORY weeks for each location/weekday/hour,
    add the buffer, round up. Sales after the last cook hour are folded into it."""
    last_cook = max(cook_hours)
    hist = defaultdict(list)
    for (d, loc), hours in demand.items():
        if until and d >= until:
            continue
        per_hour = defaultdict(int)
        for h, q in hours:
            per_hour[h if h <= last_cook else last_cook] += q
        for h in cook_hours:
            hist[(loc, d.weekday(), h)].append((d, per_hour.get(h, 0)))
    forecast = {k: statistics.mean(q for _, q in sorted(v)[-WEEKS_OF_HISTORY:])
                for k, v in hist.items()}
    plan = defaultdict(dict)
    locations = {k[0] for k in forecast}
    for loc in locations:
        # A weekday with no history for this store borrows the store's all-day average.
        for wd in range(7):
            for h in cook_hours:
                if (loc, wd, h) not in forecast:
                    same = [forecast[k] for k in forecast if k[0] == loc and k[2] == h]
                    forecast[(loc, wd, h)] = statistics.mean(same) if same else 0
    for loc, wd in {(k[0], k[1]) for k in forecast}:
        # Pieces still in the warmer from the last drop count toward this hour,
        # so the buffer doesn't pile up over the day.
        # With a 90-minute hold, chicken from the last drop can only cover about the
        # first half of this hour, then it's tossed.
        leftover = 0.0
        for h in cook_hours:
            f = forecast[(loc, wd, h)]
            usable = min(leftover, f / 2)
            need = max(f * (1 + BUFFER), f + MIN_EXTRA) - usable
            drop = max(MIN_DROP, ROUND_TO * math.ceil(need / ROUND_TO))
            plan[(loc, wd)][h] = drop
            leftover = max(0.0, drop - (f - usable))
    return plan


def backtest(demand, days, cook_hours, lbs_per_piece):
    """Walk forward through the last BACKTEST_WEEKS weeks. Each week is planned
    using only the weeks before it (up to WEEKS_OF_HISTORY), exactly as it would
    be in real life, then replayed against that week's ACTUAL Toast sales, for
    the fixed 24/hour and the new plan. No assumption about missed sales is used
    in the test, so the savings are conservative."""
    last = max(d for d, _ in days)
    first = min(d for d, _ in days)
    start = last - timedelta(days=7 * BACKTEST_WEEKS - 1)
    while (start - first).days < 14 and start <= last:   # need 2 earlier weeks
        start += timedelta(days=7)
    weeks = ((last - start).days + 1) // 7
    if weeks < 1:
        return {}, start, last, 0
    keys = ["old_waste", "new_waste", "rule_waste", "old_wait", "new_wait",
            "rule_wait", "old_waited_pcs", "new_waited_pcs", "rule_waited_pcs", "rule_extra", "old_cooked", "new_cooked"]
    res = defaultdict(lambda: dict.fromkeys(keys, 0))
    flat = {h: CURRENT_BATCH for h in cook_hours}
    for w in range(weeks):
        wk_start = start + timedelta(days=7 * w)
        plan = build_plan(demand, cook_hours, until=wk_start)
        for (d, loc), hours in days.items():
            if not (wk_start <= d < wk_start + timedelta(days=7)):
                continue
            p = plan[(loc, d.weekday())]
            # Today: 24 dropped on the hour, nothing tossed mid-day (this matches the waste log)
            w0, e0, _, n0 = simulate(hours, flat, cook_hours, lead=0)
            w1, e1, _, n1 = simulate(hours, p, cook_hours, hold=HOLD_MINUTES)
            w2, e2, x2, n2 = simulate(hours, p, cook_hours, backup=BACKUP_DROP, hold=HOLD_MINUTES)
            r = res[loc]
            r["old_waste"] += w0; r["new_waste"] += w1; r["rule_waste"] += w2
            r["old_wait"] += e0; r["new_wait"] += e1; r["rule_wait"] += e2
            r["old_waited_pcs"] += n0; r["new_waited_pcs"] += n1; r["rule_waited_pcs"] += n2
            r["rule_extra"] += x2
            r["old_cooked"] += sum(flat.values()); r["new_cooked"] += sum(p.values())
    for r in res.values():
        for k in list(r):
            r[k] = round(r[k] / weeks, 1)
    return dict(res), start, last, weeks


def drop_label(h):
    """'12:00' -> '11:48 AM': when that hour's batch goes into the fryer."""
    m = int(h[:2]) * 60 - FRY_MINUTES
    hh, mm = divmod(m, 60)
    return f"{hh % 12 or 12}:{mm:02d} {'PM' if hh >= 12 else 'AM'}"


def write_print_sheet(plan, locations, cook_hours, out):
    """One printable page per store with the week's drops, for kitchens that work
    off paper. Open print.html?store=fishtown to print just one store."""
    heads = "".join(f"<th>{DAYS[wd][:3]}</th>" for wd in range(7))
    pages = []
    for loc in locations:
        rows = "".join(
            f"<tr><td>{drop_label(h)}</td>" + "".join(f"<td>{plan[(loc, wd)][h]}</td>" for wd in range(7)) + "</tr>"
            for h in cook_hours)
        total = "".join(f"<td>{sum(plan[(loc, wd)].values())}</td>" for wd in range(7))
        slug = loc.lower().replace(" ", "-")
        pages.append(f"""<section class="page" data-store="{slug}">
  <h1>{loc}: chicken drops</h1>
  <p class="sub">Find today's column. At each time, drop that many pieces. Each batch is ready {FRY_MINUTES} minutes later, on the hour.</p>
  <table>
    <tr><th>Drop at</th>{heads}</tr>
    {rows}
    <tr class="tot"><td>Total</td>{total}</tr>
  </table>
  <div class="rule">Down to {BACKUP_AT} pieces? <b>Drop {BACKUP_DROP} more now.</b> &nbsp;|&nbsp; Chicken older than {HOLD_MINUTES} minutes: <b>toss it.</b></div>
  <p class="foot">Same numbers as the screen. The plan updates each Monday, so print a new sheet then.</p>
</section>""")
    out.write_text(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chicken Drop Sheet</title>
<style>
  :root {{ --fg:#000; --bg:#fff; --line:#000; --mute:#333; }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --fg:#fff; --bg:#111; --line:#888; --mute:#bbb; }} }}
  :root[data-theme="dark"] {{ --fg:#fff; --bg:#111; --line:#888; --mute:#bbb; }}
  body {{ margin:0; padding:16px; font-family: Arial, sans-serif; color:var(--fg); background:var(--bg); }}
  h1 {{ font-size: 28px; margin: 0 0 6px; }}
  .sub {{ font-size: 17px; margin: 0 0 12px; color: var(--mute); }}
  .wrap {{ overflow-x: auto; }}
  table {{ width:100%; border-collapse: collapse; table-layout: fixed; min-width: 520px; }}
  th, td {{ border: 2px solid var(--line); height: 44px; font-size: 22px; text-align:center; font-weight: 700; }}
  th {{ font-size: 18px; }} th small {{ font-weight: 400; font-size: 14px; }}
  th:first-child, td:first-child {{ width: 110px; font-size: 18px; }}
  .tot td {{ font-weight: 400; font-size: 16px; }}
  .rule {{ margin-top: 14px; border: 3px solid var(--line); padding: 10px; font-size: 20px; text-align: center; }}
  .foot {{ font-size: 14px; color: var(--mute); margin: 8px 0 0; }}
  .page + .page {{ margin-top: 40px; }}
  button {{ font-size:18px; padding:10px 16px; margin-bottom:12px; }}
  @page {{ size: landscape; margin: 12mm; }}
  @media print {{ body {{ padding: 0; }} button {{ display:none; }}
    :root {{ --fg:#000; --bg:#fff; --line:#000; --mute:#333; }}
    th, td {{ height: 36px; }} h1 {{ font-size: 24px; }} .rule {{ margin-top: 10px; padding: 8px; }}
    .page {{ break-after: page; }} .page:last-child {{ break-after: auto; }} .page + .page {{ margin-top: 0; }} }}
</style>
</head>
<body>
<button onclick="window.print()">Print this sheet</button>
<div class="wrap">
{chr(10).join(pages)}
</div>
<script>
  const s = new URLSearchParams(location.search).get("store");
  if (s) document.querySelectorAll(".page").forEach(p => {{ if (p.dataset.store !== s) p.remove(); }});
</script>
</body>
</html>
""")


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "data"
    sales, cook_hours, lbs_per_piece = load(folder)
    if not cook_hours:
        cook_hours = sorted({h for _, _, h, _ in sales})[:-1]
    days = by_day(sales)
    used = load_plans_used(folder)
    demand = estimate_demand(days, cook_hours, used)
    plan = build_plan(demand, cook_hours)
    bt, bt_start, last, bt_weeks = backtest(demand, days, cook_hours, lbs_per_piece)
    locations = sorted({loc for _, loc in days})

    data = {
        "locations": locations,
        "days": DAYS,
        "hours": cook_hours,
        "plan": {loc: {DAYS[wd]: plan[(loc, wd)] for wd in range(7)} for loc in locations},
        "week_of": (last + timedelta(days=1)).isoformat(),
        "data_through": last.isoformat(),
        "backtest": bt,
        "backtest_range": f"{bt_start.isoformat()} to {last.isoformat()}",
        "lbs_per_piece": round(lbs_per_piece, 2),
        "cost_per_piece": COST_PER_PIECE,
        "fry_minutes": FRY_MINUTES,
        "backup_at": BACKUP_AT,
        "backup_drop": BACKUP_DROP,
        "hold_minutes": HOLD_MINUTES,
    }
    plans_dir = Path(__file__).parent / "plans"
    plans_dir.mkdir(exist_ok=True)
    saved = plans_dir / f"plan_week_of_{data['week_of']}.csv"
    with open(saved, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["location", "day", "hour", "pieces"])
        for loc in locations:
            for wd in range(7):
                for h in cook_hours:
                    w.writerow([loc, DAYS[wd], h, plan[(loc, wd)][h]])

    template = (Path(__file__).parent / "template.html").read_text()
    out = Path(__file__).parent / "index.html"
    out.write_text(template.replace("/*DATA*/null", json.dumps(data)))

    sheet = Path(__file__).parent / "print.html"
    write_print_sheet(plan, locations, cook_hours, sheet)

    print(f"Plan for week of {data['week_of']} (data through {last})\n")
    for loc in locations:
        print(loc)
        print("  hour  " + " ".join(d[:3].rjust(4) for d in DAYS))
        for h in cook_hours:
            print(f"  {h} " + " ".join(str(plan[(loc, wd)][h]).rjust(4) for wd in range(7)))
        r = bt.get(loc)
        if not r:
            print("  (Not enough history yet to test this store.)\n")
            continue
        print(f"  Test on last {bt_weeks} weeks of real sales, per week: waste "
              f"{r['old_waste']:.0f} -> {r['new_waste']:.0f} pieces "
              f"({r['rule_waste']:.0f} with backup rule); pieces customers waited for "
              f"{r['old_waited_pcs']:.0f} -> {r['rule_waited_pcs']:.0f} with backup rule "
              f"(total minutes waiting {r['old_wait']:.0f} -> {r['rule_wait']:.0f}); cooked {r['old_cooked']:.0f} -> {r['new_cooked']:.0f}\n")
    print(f"Wrote {out} and {sheet}")
    print(f"Saved this week's plan to {saved}")


if __name__ == "__main__":
    main()
