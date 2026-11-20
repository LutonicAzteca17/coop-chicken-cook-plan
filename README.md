# Coop Chicken: hourly cook plan

A screen for above the fry line that tells the cook how many pieces of chicken to drop each hour. It replaces the fixed 24 pieces every hour with a number that follows what each hour actually sells, for each store and day of the week.

**Live screen:** https://lutonicazteca17.github.io/coop-chicken-cook-plan/

## For the kitchen

**What to do:** look at the big yellow number. Drop that many pieces at the top of the hour. That's it.

- The number changes on its own every hour.
- The line under it tells you the next drop, so you can get ready.
- If you tap a different day by accident, tap **Back to today**.
- **Running out before the next drop? Drop 4 more.** (This is on the screen too.)
- If you keep running out, or you're throwing out a lot, tell your manager.

## Setting up the screen (one time, 2 minutes)

On the tablet or TV above the fry line, open the link for your store and leave it open:

- Fishtown: https://lutonicazteca17.github.io/coop-chicken-cook-plan/?store=fishtown
- Northern Liberties: https://lutonicazteca17.github.io/coop-chicken-cook-plan/?store=northern-liberties

That's all. The screen follows the clock on the device:

- **Before 11 AM:** it shows the first drop of the day.
- **Each hour:** the big yellow number changes to that hour's drop, and the line under it shows the next one.
- **After the last drop:** it says "Done for today."
- **At midnight:** it switches to the next day. Each morning it reloads itself, so it picks up the newest weekly plan.

At the bottom, a color-coded snapshot shows the whole week (brighter yellow = busier hour). Today's column is outlined.

Make sure the device's clock is set to Eastern time. Keep the screen awake: on an iPad, set Settings > Display & Brightness > Auto-Lock to Never.

## For Pathway: updating the plan each week

Pathway does this each Monday. To do it yourself:

1. Replace `data/toast_hourly_sales.csv` with the new Toast export. It needs these columns: `date, location, hour, fried_chicken_pieces_sold`. Hours can be written as `11:00`, `9:00 PM`, and so on.
2. Run `python3 cook_plan.py`. It needs only Python 3, with nothing to install. Macs already have it.
3. Commit and push. The live screen updates within a couple of minutes, and every screen reloads it the next morning.

Each run saves the plan it made in `plans/`. The next run uses that file to know what the kitchen actually cooked, so it can tell when an hour sold out. If something is wrong with the export (for example, a missing column), the script stops and says exactly what's wrong.

## How the numbers are made

- **Forecast:** the average pieces sold in that hour, on that weekday, at that store, over the last 6 weeks.
- **Sold-out hours:** when the warmer ran empty (mostly Thu to Sun at 12 to 1 PM), the plan cooks 15% more than what sold in that hour. See "What we don't know yet" below.
- **Drop size:** the forecast plus a 30% safety margin (at least 4 extra pieces, so slow afternoon hours aren't cut too thin), minus what is probably still in the warmer from the last drop, rounded up to a multiple of 4. Sales after the last drop (9 PM) are covered by the 8 PM drop.
- All settings are at the top of `cook_plan.py`.

## Does it work?

The test uses the same 6-week window as the plan (Sep 7 to Oct 18). Each week was planned using only the weeks before it, the way it would work in real life. Then that week's **actual Toast sales** were replayed hour by hour, carrying leftovers forward like the kitchen does. No assumptions about missed sales are used in the test, so these savings are conservative.

As a check, replaying the old fixed 24/hour gives 665 pieces of waste a week, which matches the real waste log for those 6 weeks exactly.

| Per week, average of last 6 weeks | Fixed 24/hour | This plan + "drop 4 more" |
|---|---|---|
| Pieces thrown out, Fishtown | 241 | 95 |
| Pieces thrown out, Northern Liberties | 424 | 84 |
| Pieces thrown out, both stores | 665 | 179 |
| Waste cost at $1.10/piece | ~$732 | ~$197 |
| Hours the warmer ended up empty, both stores | 11 | 3 |

That's about **73% less waste, roughly $535 a week or $28K a year**, with the warmer running empty far less often at the lunch rush.

## Why "drop 4 more"

On the same 6 weeks of real sales, the plan alone leaves the warmer empty about 7.5 hours a week. "Drop 4 more" brings that down to 3, using about 15 extra pieces a week across both stores and adding only about 2 pieces of waste. "Drop 8 more" gets to about 2 empty hours, but adds about 10 pieces of waste a week. Four is the better trade.

## What we don't know yet

Toast only records what was sold, so it can't count customers who left because there was no chicken. When the warmer ran empty (about 11 hours a week, almost all at noon and 1 PM, Thursday to Sunday), the plan cooks 15% more than what sold in that hour (the `STOCKOUT_UPLIFT` setting). During the first test week, a simple in-store tally of customers who ask for chicken when none is ready will show whether that 15% should be higher or lower. Print the one-page tally sheet: https://lutonicazteca17.github.io/coop-chicken-cook-plan/tally.html

## Assumptions to confirm with the kitchen

- Chicken holds well for about an hour in the warmer, and anything left at close is thrown out. The data confirms that leftovers at close equal the waste log.
- A drop is made on the hour, as today, and is ready within that hour.
