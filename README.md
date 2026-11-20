# Coop Chicken: hourly cook plan

A screen for above the fry line that tells the cook how many pieces of chicken to drop each hour. It replaces the fixed 24 pieces every hour with a number that follows what each hour actually sells, for each store and day of the week.

**Live screen:** https://lutonicazteca17.github.io/coop-chicken-cook-plan/

## Setting up the screen (one time, 2 minutes)

Open the link for your store on the tablet or TV above the fry line, and leave it open:

- Fishtown: https://lutonicazteca17.github.io/coop-chicken-cook-plan/?store=fishtown
- Northern Liberties: https://lutonicazteca17.github.io/coop-chicken-cook-plan/?store=northern-liberties

That's all. The screen follows the clock on the device:

- **Before 11 AM:** it shows the first drop of the day.
- **Each hour:** the big yellow number changes to that hour's drop, and the line under it shows the next one.
- **After the last drop:** it says "Done for today."
- **At midnight:** it switches to the next day. Each morning it reloads itself, so it picks up the newest weekly plan.

At the bottom, a color-coded snapshot shows the whole week (brighter yellow = busier hour). Today's column is outlined.

Make sure the device's clock is set to Eastern time. Keep the screen awake: on an iPad, set Settings > Display & Brightness > Auto-Lock to Never.

## Updating the plan each week

Pathway does this each Monday. To do it yourself:

1. Replace `data/toast_hourly_sales.csv` with the new Toast export. It needs these columns: `date, location, hour, fried_chicken_pieces_sold`. Hours can be written as `11:00`, `9:00 PM`, and so on.
2. Run `python3 cook_plan.py`. It needs only Python 3, with nothing to install. Macs already have it.
3. Commit and push. The live screen updates within a couple of minutes, and every screen reloads it the next morning.

Each run saves the plan it made in `plans/`. The next run uses that file to know what the kitchen actually cooked, so it can tell when an hour sold out. If something is wrong with the export (for example, a missing column), the script stops and says exactly what's wrong.

## How the numbers are made

- **Forecast:** the average pieces sold in that hour, on that weekday, at that store, over the last 6 weeks.
- **Sold-out hours:** Toast only records what was sold. When the warmer ran empty (mostly Fri to Sun at 12 to 1 PM), real demand was higher, so those hours are counted 15% higher.
- **Drop size:** the forecast plus a 30% safety margin, minus what is probably still in the warmer from the last drop, rounded up to a multiple of 4. Sales after the last drop (9 PM) are covered by the 8 PM drop.
- All settings are at the top of `cook_plan.py`.

## Does it work?

The plan was built only from data through Sep 20, then replayed against the 4 weeks that followed (Sep 21 to Oct 18), carrying leftovers hour to hour the way the kitchen does.

| Per week | Fixed 24/hour | This plan |
|---|---|---|
| Pieces thrown out, Fishtown | 244 | 63 |
| Pieces thrown out, Northern Liberties | 421 | 67 |
| Pieces customers wanted but couldn't get (both stores) | 50 | 23 |
| Waste cost at $1.10/piece (both stores) | ~$732 | ~$143 |

That's about **$590 a week, or roughly $30K a year**, less chicken in the trash, with fewer lunch-rush sellouts.

## Assumptions to confirm with the kitchen

- Chicken holds well for about an hour in the warmer, and anything left at close is thrown out. The data confirms that leftovers at close equal the waste log.
- A drop is made on the hour, as today, and is ready within that hour.
- The 15% bump for sold-out hours is an estimate. Toast can't see customers who walked away.
