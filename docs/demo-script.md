# Demo video script (English, about 2 minutes)

Every command below has been run and checks out. Live numbers will differ on the day you record. Before you start, use a terminal at least 100 columns wide and run `weather cache clear`, so the first call is a real network request. For an Armenian version, see [demo-script.hy.md](demo-script.hy.md).

| Time | On screen | Say (in your own words) |
|---|---|---|
| 0:00–0:12 | Terminal, project folder | "This is Think Weather, a Python command-line app. Give it a city or a postal code and it shows the weather right in the terminal. It needs no API key." |
| 0:12–0:35 | `weather Yerevan --country AM --forecast` | "Here is Yerevan, live. At the top: temperature, feels-like, conditions, humidity, wind with its direction, and pressure. Below that is a chart of the next 24 hours, and then five days with a bar showing each day's low to high. The label says this is model data, and the time is Yerevan local time." |
| 0:35–0:50 | `weather Yerevan --units imperial` | "Fahrenheit and miles per hour are one flag away. The app stores everything in metric and converts only when it draws, so nothing is ever converted twice." |
| 0:50–1:05 | `weather 90210 --country US --forecast` | "Postal codes work too. Here is 90210, Beverly Hills. I pass a country because the same code can exist in several countries. Leading zeros, like 00501, are kept." |
| 1:05–1:20 | Run `weather Yerevan --country AM -v` twice | "The second run makes no weather request. You can see the cache hit. Results are saved in a small SQLite cache for ten minutes, so repeated lookups are instant and gentle on the free API." |
| 1:20–1:38 | `weather Springfield --country US`, then `weather DefinitelyNotARealCity999999 --country US` | "When a name is ambiguous, the app lists the options instead of guessing. When a place doesn't exist, I get a clear message, a hint and a specific exit code, never a crash." |
| 1:38–1:52 | `weather` (interactive): type `Yerevan`, then `u`, `f`, `q` | "Run it with no arguments and you get a simple menu. Switching units just redraws the same data, with no new request." |
| 1:52–2:10 | `pytest -q`, then `docs/architecture.md` | "There are 337 automated tests, and they never touch the real internet. Four helpers do the work: a map finder that turns a name into coordinates, a weather reporter that asks Open-Meteo, a notebook (the cache) that remembers recent answers, and a painter that draws them in the terminal." |
| 2:10–2:20 | README attribution section | "Weather data comes from Open-Meteo and geocoding from GeoNames, both CC BY 4.0. The free API is for non-commercial use, and that is stated in the README. Thanks for watching!" |

## Optional extras (for a longer walkthrough)

- `weather Yerevan --demo --forecast`: fully offline, clearly labelled **DEMO / SAMPLE DATA**.
- `weather Yerevan --output json | python -m json.tool`: clean JSON with explicit units.
- `weather Yerevan --plain --forecast`: ASCII-only output for basic terminals and logs.
- `weather 0010 --country AM`: an honest "not found", with the hint to search by city. Armenian postcodes are not in the public indexes used.

## Recording checklist

- [ ] Run `weather cache clear` before recording.
- [ ] The terminal is at least 100 columns wide, uses a dark theme, and has a font with emoji support (Windows Terminal + Cascadia works).
- [ ] Show only commands from this script; don't claim the demo data is live.
- [ ] Upload to Loom or Google Drive, set the link to "anyone with the link can view", and test it in a private window before sending.
