# Spiking neural network (SNN): where it fits and how much it helps

> **Status: proposed 2026-10-16, not built yet.** Decisions still open are listed in [PROPOSAL_2026-10-16.md](PROPOSAL_2026-10-16.md); build order is there too.

A spiking neural network sends short pulses over time, the way nerve cells do.

### Verdict first
- On Botsy's data, an SNN is a learning project, not a business tool. I expect none of these roles to beat simple statistics in the next 6-12 months.
- **Why:**
  - Botsy runs 1-3 times a day, has weekly snapshots and few sales, so most hours have no events at all.
  - TSB-AD (NeurIPS 2024: 1,070 time series, 40 datasets, 40 methods) found "simpler architectures and statistical methods often yield better performance" (verified 2026-10-16).
  - SNNs save energy only on special neuromorphic chips. On our ordinary 2-processor server they bring no speed or cost benefit.
- **The plan:**
  1. Build the simple checks first; the watchdog needs them.
  2. Log everything.
  3. If you want the SNN for learning, run it in shadow (notes only), with a time limit (about 2 days to build), off the critical path.
  4. It never takes actions.

### Which library
- "Jellyspike" doesn't exist; it almost certainly means SpikingJelly (Peking University; Science Advances, October 2023).
- **SpikingJelly, not now:**
  - The GitHub code moved to Apache-2.0 on Oct 1, 2026 (PR #766). But the change itself says the records "do not establish the authorization coverage of all retained substantive historical contributions". Reviewers also flagged that the project's contribution rules don't relicense older work, and that some included code (QKFormer) has no permission grant. So even a pinned "Apache-2.0" version isn't cleanly licensed.
  - The PyPI packages are classed "Other/Proprietary License" (the Open-Intelligence license, which requires commercial users to register publicly). 2.0.0rc1 (Aug 29, 2026) is only a pre-release.
- **BindsNET, skip:** AGPL-3.0, no PyPI release between Feb 2020 and Sep 2026, heavy dependencies with an OpenCV clash, about 528 MB just to load.
- **snnTorch 1.0.0, MIT (use this):** small, no extra dependencies. It has no reward-learning rule, which the first role doesn't need.
- Revisit SpikingJelly only when a stable release ships with a settled license. This is not legal advice.

### How it fits: reflexes and reasoning
- **Watchdog = reflexes:** fixed rules that act (retry, pause paid work, alert).
- **Sensors = senses:** simple statistics (and, in shadow, the SNN) score how unusual things look; they never act.
- **Wake-up gate = attention:** a free filter decides when to spend Hermes's tokens.
- **Hermes = reasoning:** it explains and proposes.
- **You = decisions:** risky steps still need your code.

**Example:**
- Tuesday 03:00: every run "succeeded", but packs per run halved and cost per pack doubled.
- That is a one-line watchdog rule: cost per pack, or retries per page, above twice the 14-day median. No learning is needed.
- The watchdog wakes Hermes, which explains: "Image step retried 3 times per page since Monday. Pause paid work? [Pause] [Watch one more day]"

### Roles, ranked

**0. Simple sensors (not an SNN; build first)**
- **Job:**
  - an hourly record of event counts and spending, so history survives log rotation;
  - a per-run profile: duration, cost per pack, retries per page, items made, errors;
  - checks:
    - per-run ratios against 14-day medians (robust z-scores);
    - a Poisson or negative-binomial baseline for each hour of the week, for counts;
    - a stall timer.
- **Testing:** a fault-replay tool adds fake problems to real weeks: 6-hour and 24-hour stalls, error bursts, 3x and 10x spending spikes, duplicate drafts. Decide the fault types before testing.
- **Value:** high. It catches most real faults, within days.

**1. SNN reflex sensor (optional, shadow, snnTorch)**
- **Job:** a small spiking network scores how unusual each run and hour looks, using our own event and spending records.
- **Data and time:** at least 8 weeks of records (4 weeks is only 4 weekly cycles), then 8-12 weeks in shadow.
- **Cost:**
  - about 1.1 GB of disk for the PyTorch environment;
  - 340-550 MB of memory peak while it runs, measured in a test container, not on Lightsail;
  - about 40 ms to score a week.
- **Value:** low. The simple sensors will probably do as well.

**2. Hermes wake-up gate**
- Fires on watchdog rules and simple-sensor scores. Errors and budget stops always wake Hermes.
- **Value:** medium. It protects Hermes's budget; the value comes from the gate, not from any SNN.

**3. Page pre-screen (coloring and activity pages)**
- **Steps:**
  1. Collect labels: QA failures, your approve and reject reasons, and a weekly "bad pages: 3, 7" reply.
  2. Train a simple model on Botsy's existing line-art measurements.
  3. Optionally train a small image model with PyTorch (on the PC, in Actions, or in the SNN's environment) and export it to ONNX, so onnxruntime can run it.
- The SNN version would run only in shadow.
- **Data:** roughly 500 labeled pages including 50 or more bad ones (a rule of thumb), which is about 2-4 months.
- **Value:** medium for the simple model (it saves your review time, not money); low for the SNN.

**4. Production-split learner**
- Thompson sampling across niches and pack sizes, using only our own shop's results. Its proposal goes to the production director and to you.
- A fair test needs 200 or more logged decisions (6-12 months or more).
- The SNN version (reward-modulated STDP) is a poor fit: results arrive weeks late and decisions are few.

**5. Theme trend labels**
- Rising, steady, fading or seasonal, from the weekly snapshots, using Theil-Sen slopes and Mann-Kendall tests plus the season calendar. These are statistics, not a trained model.
- An SNN would need at least 52 weekly snapshots to learn seasons.

### Safety rules for every SNN or trained-model job
- **Our own data only:**
  - the event log, the spending record, our shop's stats, our own pages, and QA and review labels;
  - no licensed research numbers in any trained model: not BookBeam (its terms bar using it to "build, train... any competing product"), and not EverBee or eRank. Their terms don't mention training (EverBee) or were never checked (eRank), and no job here needs those numbers;
  - never other sellers' rows, titles or images.
- **Isolated:**
  - its own Linux user and Python environment;
  - no network and no access to .env;
  - read-only access to its inputs;
  - memory capped at 768 MB, lowest processor priority;
  - it skips its run while Botsy is running.
- **Advisory only.** All actions still go through the watchdog's rules and your codes.

### Testing and promotion
- **Shadow:** scores are saved next to the simple sensors' scores and shown only in a weekly report.
- **Promote only if, over at least 8 weeks, all of these hold:**
  1. on replayed faults decided in advance, it catches more than the simple sensors with the same or fewer false alarms (at most 1 a week);
  2. it caught at least 2 real incidents that you confirmed, earlier or that the simple sensors missed. A single incident is too few to judge;
  3. it never crashed or ran out of memory.
- **After promotion:** its flags may reach Discord as "advisory" and can wake Hermes. It still takes no actions.
- **No win after 12 weeks:** archive it, or keep it in shadow if you want it for learning.
- **Page pre-screen:** use the simple model once it catches at least 90% of bad pages. An SNN replaces it only if it does better.
- **Split learner:** only after 200 or more decisions and a statistically sound gain.
