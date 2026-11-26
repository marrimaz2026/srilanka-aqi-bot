# 🇱🇰 Sri Lanka Air Quality Bot (serious alerts, 100% free)

An X (Twitter) bot that checks the **US AQI** for Colombo every hour (more cities can be switched on in `aqi_bot.py`) using the
[IQAir AirVisual API](https://www.iqair.com/air-pollution-data-api), and posts to X through
**Typefully** only when the air gets **serious**. Everything runs on free plans:

| Service | Plan | Cost |
|---|---|---|
| IQAir | Community (free) | $0 |
| GitHub Actions | free (use a **public** repo) | $0 |
| Typefully | Free (about 10 posts/month) | $0 |

You don't need the X API, so there are no X API credits to buy.

## When does it post?

| What happens | Posted? |
|---|---|
| A city reaches **Unhealthy (151+)** or worse | ✅ alert ⬆️ |
| A city that is already Unhealthy+ gets worse or better (e.g. Unhealthy → Very Unhealthy) | ✅ ⬆️ / ⬇️ |
| A city drops back **below 151** ("all clear") | ✅ ⬇️ |
| Changes between Good / Moderate / Unhealthy for Sensitive Groups | ❌ recorded only |
| The AQI number changes but the level stays the same | ❌ |

Example post:

```
🇱🇰 Sri Lanka air quality update (03:00 PM SL time)
🟣 Galle: AQI 240 (Very Unhealthy) ⬆️
🔴 Colombo: AQI 165 (Unhealthy) ⬆️
Avoid outdoor activity. Keep windows closed.
Data: IQAir
#SriLanka #AirQuality
```

**Protecting your free posts:**
- **Daily limit:** at most **1 "worse" post and 1 "better" post per day** (Sri Lanka time).
  If another city gets worse after the day's "worse" post, it's held back and posted the next
  day if it's still true. This also stops an AQI that bounces between 150 and 151 from using up
  your posts.
- **Quota check:** before posting, the bot asks Typefully how many posts you have left. If there
  aren't enough, it skips the post rather than failing. It won't post a pile of old alerts when
  your quota resets.
- All cities that change in the same hour go into **one** post.

You can change the alert level at the top of `aqi_bot.py` (`SERIOUS_LEVEL`).

## Files

| File | What it does |
|---|---|
| `aqi_bot.py` | The bot. Edit `CITIES` and the alert settings at the top. |
| `list_cities.py` | One-time helper: prints every Sri Lankan city name IQAir has. |
| `.github/workflows/aqi.yml` | Runs the bot every hour and saves `state.json`. |
| `requirements.txt` | Python packages. |
| `notify_telegram.py` | Sends you a Telegram message after each run (optional). |
| `tests/` | Offline tests with fake data (no API calls, no posts, no messages). |
| `state.json` | Created automatically on the first run. Don't edit it by hand. |

---

## Setup (step by step)

### 1. Get a free IQAir API key
1. Go to <https://dashboard.iqair.com/> and create a free account.
2. Open **API** and create a key on the free **Community** plan.
3. Copy the key. This is your `IQAIR_API_KEY`.

The bot uses 1 call per hour (~720/month) for Colombo, far inside the free 10,000/month. With all 7 cities it would be ~5,000/month,
and it waits 13 seconds between calls to stay under 5 calls/minute.

### 2. Set up Typefully (free)
1. Sign in at <https://typefully.com/> and connect the X account the bot should post from.
2. Open **Settings → API** (in some versions it's under **Integrations**) and create an API key.
   Make sure the key is allowed to **publish**. Copy it. This is your `TYPEFULLY_API_KEY`.
3. If your Typefully account has **only one** connected account, you're done, because the bot
   finds it by itself. If you have more than one, the first run prints a list like
   `12345 (@yourname)`. Put that number in a secret called `TYPEFULLY_SOCIAL_SET_ID`.

Check your plan's monthly post limit in Typefully. The bot reads it automatically, so if the
limit changes nothing breaks.

### 3. Put the code on GitHub
1. Create a new **public** repository on GitHub. Public repos get unlimited free Actions
   minutes. Your keys stay private as secrets, and `state.json` only contains AQI levels.
2. Upload all the files from this folder, **including the hidden `.github` folder**
   (**Add file → Upload files**, then drag everything in, or use `git push`).

### 4. Add the secrets
In your repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Name | Value |
|---|---|
| `IQAIR_API_KEY` | your IQAir key |
| `TYPEFULLY_API_KEY` | your Typefully API key |
| `TYPEFULLY_SOCIAL_SET_ID` | *(only if step 2.3 told you to)* |

Never paste keys into the code. The bot only reads them from these secrets.

### 5. Check the city names (recommended)
```bash
pip install -r requirements.txt
IQAIR_API_KEY=your_key_here python list_cities.py
```
(On Windows PowerShell: `$env:IQAIR_API_KEY="your_key_here"; python list_cities.py`)

It takes about 2 minutes and prints ready-to-paste lines like `("Colombo", "Western"),`.
If a city in `CITIES` isn't listed, swap it for one that is. A wrong name shows up in the
bot's log as `API error 'city_not_found' - skipping`.

### 6. Test with "Run workflow" (nothing is posted)
1. Go to the **Actions** tab. If asked, enable workflows.
2. Click **Sri Lanka AQI bot** → **Run workflow**, leave **Dry run** ticked ✅, and run it.
3. Open the run → **Run bot** step. You'll see every city's AQI and, if any city is serious,
   the exact post it *would* send. Most of the time it will say `No serious changes`, which
   is normal when the air is fine.

### 7. Go live
Run the workflow once more with **Dry run unticked**. The first live run saves every city's
level, and posts only if a city is already at Unhealthy (151+) or worse. After that it runs
every hour by itself.

---

## How it works

- **Levels** (US AQI): Good 0–50 🟢, Moderate 51–100 🟡, Unhealthy for Sensitive Groups 101–150 🟠,
  Unhealthy 151–200 🔴, Very Unhealthy 201–300 🟣, Hazardous 301+ 🟤.
- Posts are sorted worst AQI first and include the advice for the worst level.
- Posts stay at 270 characters or less (emoji count as 2). Longer updates become a thread.
- `state.json` stores each city's current level and which of today's two posts are used. When a post is
  needed, the state is saved only after Typefully accepts the post, so a failed post is retried
  the next hour.
- If a city fails (wrong name, API error, or a result that isn't from Sri Lanka), it's skipped
  and its old state is kept. `call_limit_reached` waits 60 seconds and retries once.

## Telegram status messages (optional, free)

After every run, the bot can send you a Telegram message so you know it's working:

| Message | When | Sound? |
|---|---|---|
| ✅ AQI bot is working | normal run, no post needed | silent |
| 🧪 AQI bot dry run OK | a dry run worked | silent |
| 📢 AQI bot posted to X | a post went out | 🔔 |
| ⚠️ AQI bot warning | no data from IQAir, or Typefully quota used up | 🔔 |
| ❌ AQI bot FAILED | any step failed (shows which step) | 🔔 |

Each message shows Colombo's AQI and a link to the run on GitHub.

**Setup:**
1. In Telegram, open **@BotFather**, send `/newbot`, and follow the steps. Copy the **token** it gives you.
2. Open your new bot's chat and press **Start** (send it any message).
3. Get your chat id: open **@userinfobot** in Telegram and press Start. It replies with your **Id** (a number).
4. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**, add
   `TELEGRAM_BOT_TOKEN` (the token) and `TELEGRAM_CHAT_ID` (the number).
5. Test: **Actions → Sri Lanka AQI bot → Run workflow** (Dry run ticked). You should get
   "🧪 AQI bot dry run OK".

**Fewer messages:** add a repository **variable** (Variables tab) `TELEGRAM_NOTIFY` = `important`.
Then you only get posts, warnings and failures. Delete it to get every run again.

If the Telegram secrets aren't set, this step does nothing. A Telegram problem never makes the bot fail.

## Options

- **Keep hourly runs in dry-run mode:** Settings → Secrets and variables → Actions → **Variables**
  tab → add `DRY_RUN` = `true`. Delete it to go live.
- **Run locally:** `DRY_RUN=true IQAIR_API_KEY=your_key python aqi_bot.py`
- **Run the tests:** `python -m unittest discover -s tests -v`
- **Alert at a different level:** change `SERIOUS_LEVEL` in `aqi_bot.py`
  (2 = from 101, 3 = from 151, 4 = from 201). A lower number means more posts.
- **Start over:** delete `state.json` from the repo.

## Good to know

- GitHub's hourly schedule can run a few minutes late at busy times. That's normal.
- On public repos, GitHub pauses scheduled workflows after 60 days with no repository activity.
  The bot's `state.json` commits usually keep it active. If it ever stops, re-enable it in the
  Actions tab.
- Typefully's free plan and its limits are set by Typefully and can change.
- Data: IQAir AirVisual. AQI shown is the US EPA scale.
