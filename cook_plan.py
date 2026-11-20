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
BUFFER = 0.30            # cook 30% above the forecast (tested: best balance of waste vs running out)
ROUND_TO = 4             # drop in multiples of 4 pieces
MIN_DROP = 4             # never drop fewer than this in a cook hour
STOCKOUT_UPLIFT = 1.15   # when an hour sold out, assume real demand was 15% higher
COST_PER_PIECE = 1.10    # what one raw piece costs (from Dre)
CURRENT_BATCH = 24       # today's fixed batch, used for the before/after comparison
BACKTEST_WEEKS = 4       # weeks held out to test the plan
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def load(folder):
    folder = Path(folder)
    sales = []
    with open(folder / "toast_hourly_sales.csv", newline="") as f:
        for r in csv.DictReader(f):
            sales.append((date.fromisoformat(r["date"]), r["location"].strip(),
                          r["hour"].strip(), int(r["fried_chicken_pieces_sold"])))
    cook_hours = []
    sched = folder / "current_cook_schedule.csv"
    if sched.exists():
        with open(sched, newline="") as f:
            cook_hours = [r["cook_time"].strip() for r in csv.DictReader(f)]
    lbs_per_piece = 0.42
    waste = folder / "waste_log.csv"
    if waste.exists():
        with open(waste, newline="") as f:
            rows = list(csv.DictReader(f))
        pcs = sum(int(r["pieces_discarded"]) for r in rows)
        lbs = sum(float(r["lbs_discarded"]) for r in rows)
        if pcs:
            lbs_per_piece = lbs / pcs
    return sales, cook_hours, lbs_per_piece


def by_day(sales):
    """{(date, location): [(hour, sold), ...]} in hour order."""
    days = defaultdict(list)
    for d, loc, h, q in sales:
        days[(d, loc)].append((h, q))
    for k in days:
        days[k].sort()
    return days


def simulate(hours_sold, plan, cook_hours):
    """Run one day: cook `plan[hour]` at each cook hour, sell, carry leftovers.
    Returns (pieces thrown out at close, pieces short in each hour that ran out)."""
    on_hand, sold_out = 0, []
    for h, demand in hours_sold:
        on_hand += plan.get(h, 0) if h in cook_hours else 0
        sold = min(on_hand, demand)
        on_hand -= sold
        if sold < demand:
            sold_out.append(demand - sold)
    return on_hand, sold_out


def estimate_demand(days, cook_hours):
    """Toast only records what was sold. When the fixed 24/hour ran out, the
    real demand was higher, so those hours are scaled up."""
    flat = {h: CURRENT_BATCH for h in cook_hours}
    demand = {}
    for key, hours in days.items():
        on_hand, est = 0, []
        for h, q in hours:
            on_hand += flat.get(h, 0)
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
    for loc, wd in {(k[0], k[1]) for k in forecast}:
        # Pieces still in the warmer from the last drop count toward this hour,
        # so the buffer doesn't pile up over the day.
        leftover = 0.0
        for h in cook_hours:
            f = forecast[(loc, wd, h)]
            need = f * (1 + BUFFER) - leftover
            drop = max(MIN_DROP, ROUND_TO * math.ceil(need / ROUND_TO))
            plan[(loc, wd)][h] = drop
            leftover = max(0.0, leftover + drop - f)
    return plan


def backtest(demand, cook_hours, lbs_per_piece):
    """Build the plan from earlier weeks, then replay the last BACKTEST_WEEKS
    weeks against it and against the fixed 24/hour."""
    last = max(d for d, _ in demand)
    start = last - timedelta(days=7 * BACKTEST_WEEKS - 1)
    plan = build_plan(demand, cook_hours, until=start)
    flat = {h: CURRENT_BATCH for h in cook_hours}
    res = defaultdict(lambda: {"old_waste": 0, "new_waste": 0, "old_out": 0, "new_out": 0,
                               "old_cooked": 0, "new_cooked": 0, "old_short": 0, "new_short": 0})
    for (d, loc), hours in demand.items():
        if d < start:
            continue
        p = plan[(loc, d.weekday())]
        w0, o0 = simulate(hours, flat, cook_hours)
        w1, o1 = simulate(hours, p, cook_hours)
        r = res[loc]
        r["old_waste"] += w0; r["new_waste"] += w1
        r["old_out"] += len(o0); r["new_out"] += len(o1)
        r["old_short"] += sum(o0); r["new_short"] += sum(o1)
        r["old_cooked"] += sum(flat.values()); r["new_cooked"] += sum(p.values())
    for r in res.values():
        for k in list(r):
            r[k] = round(r[k] / BACKTEST_WEEKS, 1)
        r["old_waste_lbs"] = round(r["old_waste"] * lbs_per_piece)
        r["new_waste_lbs"] = round(r["new_waste"] * lbs_per_piece)
    return dict(res), start, last


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "data"
    sales, cook_hours, lbs_per_piece = load(folder)
    if not cook_hours:
        cook_hours = sorted({h for _, _, h, _ in sales})[:-1]
    days = by_day(sales)
    demand = estimate_demand(days, cook_hours)
    plan = build_plan(demand, cook_hours)
    bt, bt_start, last = backtest(demand, cook_hours, lbs_per_piece)
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
    template = (Path(__file__).parent / "template.html").read_text()
    out = Path(__file__).parent / "index.html"
    out.write_text(template.replace("/*DATA*/null", json.dumps(data)))

    print(f"Plan for week of {data['week_of']} (data through {last})\n")
    for loc in locations:
        print(loc)
        print("  hour  " + " ".join(d[:3].rjust(4) for d in DAYS))
        for h in cook_hours:
            print(f"  {h} " + " ".join(str(plan[(loc, wd)][h]).rjust(4) for wd in range(7)))
        r = bt[loc]
        print(f"  Test on last {BACKTEST_WEEKS} weeks, per week: waste {r['old_waste']:.0f} -> "
              f"{r['new_waste']:.0f} pieces; sold-out hours {r['old_out']:.0f} -> {r['new_out']:.0f}; "
              f"pieces short {r['old_short']:.0f} -> {r['new_short']:.0f}; cooked {r['old_cooked']:.0f} -> {r['new_cooked']:.0f}\n")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
