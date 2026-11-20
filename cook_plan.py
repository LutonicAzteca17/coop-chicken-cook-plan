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
BUFFER = 0.20            # cook 20% above the forecast (tested with the 90-minute hold: best balance of waste vs running out)
ROUND_TO = 4             # drop in multiples of 4 pieces
MIN_DROP = 4             # never drop fewer than this in a cook hour
MIN_EXTRA = 4            # ...but always at least 4 pieces above the forecast (protects slow afternoon hours)
STOCKOUT_UPLIFT = 1.15   # ASSUMPTION: when the warmer ran empty, plan 15% above what sold there.
                         # Toast can't see walk-aways; the in-store tally will confirm or change this.
COST_PER_PIECE = 1.10    # what one raw piece costs (from Dre)
CURRENT_BATCH = 24       # today's fixed batch, used for the before/after comparison
BACKTEST_WEEKS = 6       # weeks replayed to test the plan (same 6-week window the forecast uses)
HOLD_MINUTES = 90        # Coop's rule: chicken is tossed after 90 minutes under the heat lamp
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


def simulate(hours_sold, plan, cook_hours, backup=0, hold=None):
    """Replay one day of real Toast sales in half-hour steps. Each drop goes in at
    the top of its hour. Oldest chicken is sold first. With `hold` (minutes), any
    piece older than that is tossed. With backup > 0, the cook drops that many
    more whenever the warmer is empty and customers are waiting during a cook hour.
    Returns (pieces thrown out, hours the warmer ended empty, extra pieces dropped)."""
    batches, tossed, empty, extra = [], 0, 0, 0     # batches: [minute cooked, pieces]
    for h, sold in hours_sold:
        start = int(h[:2]) * 60
        if h in cook_hours and plan.get(h, 0):
            batches.append([start, plan[h]])
        halves = [sold // 2, sold - sold // 2]
        for i, want in enumerate(halves):
            now = start + 30 * i
            if hold:
                for bt in batches:
                    if now - bt[0] > hold - 30:      # would pass the limit during this half hour
                        tossed += bt[1]; bt[1] = 0
                batches = [bt for bt in batches if bt[1] > 0]
            if backup and h in cook_hours and want > sum(bt[1] for bt in batches):
                batches.append([now, backup]); extra += backup
            while want and batches:
                take = min(want, batches[0][1])
                batches[0][1] -= take; want -= take
                if batches[0][1] == 0:
                    batches.pop(0)
        if h in cook_hours and not batches:
            empty += 1
    tossed += sum(bt[1] for bt in batches)
    return tossed, empty, extra


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
    keys = ["old_waste", "new_waste", "rule_waste", "old_empty", "new_empty",
            "rule_empty", "rule_extra", "old_cooked", "new_cooked"]
    res = defaultdict(lambda: dict.fromkeys(keys, 0))
    flat = {h: CURRENT_BATCH for h in cook_hours}
    for w in range(weeks):
        wk_start = start + timedelta(days=7 * w)
        plan = build_plan(demand, cook_hours, until=wk_start)
        for (d, loc), hours in days.items():
            if not (wk_start <= d < wk_start + timedelta(days=7)):
                continue
            p = plan[(loc, d.weekday())]
            w0, e0, _ = simulate(hours, flat, cook_hours, hold=HOLD_MINUTES)
            w1, e1, _ = simulate(hours, p, cook_hours, hold=HOLD_MINUTES)
            w2, e2, x2 = simulate(hours, p, cook_hours, backup=BACKUP_DROP, hold=HOLD_MINUTES)
            r = res[loc]
            r["old_waste"] += w0; r["new_waste"] += w1; r["rule_waste"] += w2
            r["old_empty"] += e0; r["new_empty"] += e1; r["rule_empty"] += e2
            r["rule_extra"] += x2
            r["old_cooked"] += sum(flat.values()); r["new_cooked"] += sum(p.values())
    for r in res.values():
        for k in list(r):
            r[k] = round(r[k] / weeks, 1)
    return dict(res), start, last, weeks


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
              f"({r['rule_waste']:.0f} with drop-{BACKUP_DROP} rule); warmer empty "
              f"{r['old_empty']:.0f} -> {r['new_empty']:.0f} hours "
              f"({r['rule_empty']:.0f} with rule); cooked {r['old_cooked']:.0f} -> {r['new_cooked']:.0f}\n")
    print(f"Wrote {out}")
    print(f"Saved this week's plan to {saved}")


if __name__ == "__main__":
    main()
