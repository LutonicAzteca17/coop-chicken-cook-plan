# Coop Chicken: hourly cook plan

A fry-station screen that tells the kitchen how many pieces of chicken to drop each hour, for each location and day of the week. It replaces the fixed 24 pieces every hour.

**Open `index.html`.** Tap your store. The big yellow number is what to drop right now. The list below it is the rest of today. At the bottom, a color-coded snapshot shows the whole week (brighter yellow = busier hour). It works on a phone, tablet, or a screen above the fry line.

## Updating it each week

1. Put the new Toast export in `data/` as `toast_hourly_sales.csv`. Same columns: `date, location, hour, fried_chicken_pieces_sold`. Optionally add the new `waste_log.csv` and `current_cook_schedule.csv`.
2. Run `python3 cook_plan.py`. It needs only Python 3, with no installs.
3. `index.html` is rebuilt with next week's plan. The terminal prints the plan and the test results.

## How the numbers are made

- **Forecast:** the average pieces sold in that hour, on that weekday, at that store, over the last 6 weeks.
- **Sold-out hours:** Toast only records what was sold, so when the old 24-piece batch ran out (mostly Fri to Sun at 12 to 1 PM), real demand was higher. Those hours are counted 15% higher.
- **Drop size:** the forecast plus a 30% safety margin, minus what is probably still in the warmer from the last drop, rounded up to a multiple of 4. Sales after the last drop (9 PM) are covered by the 8 PM drop.
- All settings are at the top of `cook_plan.py`.

## Does it work?

The plan was built from data through Sep 20, then replayed against the 4 weeks that followed (Sep 21 to Oct 18), with leftovers carried hour to hour the way the kitchen does.

| Per week | Fixed 24/hour | This plan |
|---|---|---|
| Pieces thrown out, Fishtown | 244 | 63 |
| Pieces thrown out, Northern Liberties | 421 | 67 |
| Pieces customers wanted but couldn't get (both stores) | 50 | 23 |
| Waste cost at $1.10/piece (both stores) | ~$732 | ~$143 |

That's about **$590 a week, or roughly $30K a year**, less chicken in the trash, with fewer lunch-rush sellouts.

## Assumptions to confirm with the kitchen

- Chicken holds well for about an hour in the warmer, and anything left at close is thrown out (the data confirms leftovers at close equal the waste log).
- Drop to ready time fits within the hour. A drop is made on the hour, as today.
- The 15% bump for sold-out hours is an estimate. Toast can't see customers who walked away.
