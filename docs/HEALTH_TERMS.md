# Health topics: medical vs lifestyle

## Decided 2026-10-17 (built; this header overrides the design below where they differ)

The user's final answers of 2026-10-17 settle the open questions. What is built:

- **Medical topics are OUT on Etsy and KDP** (hard block everywhere: themes, briefs, listing
  text, printed text, file names, image prompts, search lists, KDP fields): medical log books
  (blood pressure, blood sugar/glucose, medication/pill, symptom, pain, migraine, chronic-illness
  trackers and the like), named diseases and disorders (depression, PTSD, eating disorders, ...),
  clinical worksheets (CBT, DBT), fertility/pregnancy medicine, weight-loss drugs, and
  disease- or medicine-themed stickers (awareness ribbons, pink ribbon, pills, syringes,
  vaccines). The list is `config/health_terms.txt` (plain lines).
- **Lifestyle journals and logs are IN**, described by layout and look: meditation, mindfulness,
  stress, exercise/workout/fitness/yoga/running, sleep, mood, gratitude, habit, water, self-care,
  food diaries, **anxiety and mental-health journals**, ADHD planners. None of these words is in
  the health file. "stress relief" is ALLOWED as a descriptive term (so section 4.6 below no
  longer applies: research keeps it, titles and tags may use it, the stationery renderer prints
  it). "relaxing", "calm", "mindful", "ADHD planner" are fine.
- **Borderline (review, never blocked)**, `?` lines in the health file: period/menopause/
  pregnancy/newborn trackers, weight-loss/calorie trackers, sobriety and recovery, grief,
  autism/dyslexia items, burnout, plus a few uncertain ones (trauma, supplements, counselor
  roles, stethoscope, hospital, bare "disease", which TTRPG sheets may print for game
  diseases; named diseases and disease trackers stay hard). "injection" blocks only as a
  phrase (injection log/tracker/site), so "fuel injection" passes. No "health journal" entry: a mental health journal is allowed.
  A flagged item waits in the review queue with the reason "<term> (health topic)".
- **Still never, on any topic (the claims rule, `core/health.py`):** claims to treat, cure,
  diagnose, heal, prevent or manage a condition, or to reduce/relieve/improve a symptom or
  state in word order ("reduces anxiety", "relieves migraine pain", "manage your ADHD
  symptoms", "improves sleep", "boosts your mood", "cures stress"); noun-form claims
  ("anxiety relief", "pain relief", "mood booster", "anti-anxiety", "lose weight"); proof and
  authority claims ("clinically proven", "doctor recommended", "therapist approved", "FDA").
  Allowed because they name no target: "ADHD planner to manage your day", "mood tracker to
  manage your month", "anxiety journal with daily check-in pages", "food diary". Focus,
  attention, concentration and memory are claim targets only after a determiner ("improve your
  focus"), so kids' learning copy ("a matching game to improve memory") passes. A claim split
  by a line break is still a claim. QA lets the candy word "treat"/"treats" print on kids'
  pages (a Halloween word search); "treats anxiety" is still a claim.
- **Where it runs:** `core.compliance.Blocklist.load` reads the health file next to the
  trademark file (settings `compliance.health_terms`, default `config/health_terms.txt`), so
  every stage gets the topics and `Blocklist.label()` says "health topic". `listing.rules.medical`
  = claim words + medical topics + `core.health.claim_problems`; a model draft naming a medical
  topic is repaired, not stopped as a trademark. Research cuts `core.health.research_cut_terms()`
  from theme names (not "stress relief"). The stationery renderer runs the claims check next
  to its `MEDICAL_RE` (which no longer refuses anxiety, depression, mental health or stress
  relief: topics come through the blocklist). QA's `compliance_file_claims` checks printed text
  per niche: listing rule for coloring/stickers/activity, each generator's `text_problems` for
  stationery and ttrpg, so "Medicine (Wis)", "Sanity", "Major Wound" keep printing.
- Not built here: the search-list note "(check: health?)" (`service/searchlist.py`, requested)
  and the KDP search list.

> **Design below: written 2026-10-17 before the final answers.** Where it differs from the
> header (food diaries, anxiety and mental health, "stress relief", the borderline list), the
> header wins. Open choices and build order: [PROPOSAL_2026-10-16.md](PROPOSAL_2026-10-16.md) section "Round 2".

Status: design only. I read the repo and changed nothing. Another build is editing these files now. Uncommitted changes: `listing/__init__.py`, `listing/rules.py`, `research/defaults.py`, `research/pipeline.py`, `core/compliance.py`, `core/briefs.py`, `qa/file_text.py`, `config/trademark_blocklist.txt`, `config/settings.yaml`. Untracked (new) files: `generators/stationery/`, `generators/ttrpg/`, `config/ttrpg_systems.yaml`. Line numbers below may move.

## 1. Your decision, in plain words

You said: "Any medical yes but lifestyle log books or journals should be fine mediation or stress, excercise logs are all fine".

What that means for Botsy:
- Botsy leaves out medical products, such as blood pressure, blood sugar, medication, symptom and therapy logs.
- Lifestyle journals and logs are fine, such as meditation, stress, exercise, mood, sleep and habits.
- The rule "no medical or therapeutic claims" stays. A lifestyle product describes its layout and its look. It never promises a health result.

## 2. What Botsy does today (re-checked)

### 2.1 Where the rules live
- **Research** (`research/defaults.py`):
  - `DEFAULTS["medical_terms"]` (about lines 99-107) lists claim words, such as treat, cure, heal, therapy, medication, "anxiety relief" and "pain relief".
  - Research cuts these words out of theme names. A row that contains one still counts toward its other themes.
  - `MEDICAL_OK_PHRASES` (line 220) holds safe phrases like "trick or treat".
- **Listing** (`listing/__init__.py`, about lines 86-98):
  - The same claim words, plus safe phrases.
  - Settings can add words, never remove them.
  - `listing/rules.py` `medical()` (line 104) does the check. The validator message is at about line 268.
  - The model prompt rule is at `listing/model.py` lines 83-85.
- **Publisher:** `_medical_problems` (line 257) reuses `listing.rules.medical`.
- **Stationery page renderer** (`generators/stationery/render.py`): `MEDICAL_RE` (lines 33-35) is stricter than the listing check. It also refuses symptom(s), disorder(s), anxiety, depression, "mental health" and "stress relief". It does not have "pain relief".
- **TTRPG page renderer** (`generators/ttrpg/render.py`): `MEDICAL_RE` (lines 35-37) is lighter on purpose. Game words like the "Medicine" skill must print.
- **QA** (`qa/file_text.py`): it checks printed text against the blocklist only. There is no medical check.
- **Blocklist:** `config/trademark_blocklist.txt` has no health terms.
- **Tests that keep the lists in step:**
  - `tests/listing/test_packs.py` (lines 328-480, including `test_every_research_claim_word_is_a_listing_claim_word`)
  - `tests/research/test_packs_research.py` (lines 168-192 and 467-492)
  - `tests/generators_stationery/test_compliance.py`

### 2.2 What gets through today (I ran these checks again)

| Text | Listing check | Research | Stationery page |
|---|---|---|---|
| Stress Relief Coloring Pages | passes | "stress", "relief" and "stress relief" become theme words | refused |
| Anxiety Journal | passes | "anxiety" becomes a theme word | refused |
| Blood Pressure Log Book | passes | theme words, if the row lands in a niche | passes |
| Symptom Tracker, Migraine Diary, CBT Worksheet, Period Tracker, Weight Loss Tracker | pass | as above | partly |
| relieves stress / reduces anxiety / helps manage stress | pass | - | - |
| Medication Log | caught | word cut | refused |

Research only sees a medical row when its title also matches a niche rule.
- "Health Planner Printable, Medication Tracker, Blood Pressure Log" lands in stationery, because of "planner".
- "Blood Pressure Log Book" alone lands in no niche.
- Etsy titles are often stuffed with keywords, so medical rows do reach research.

### 2.3 Gaps
1. Medical topics are not blocked anywhere. Only claim words are.
2. Result claims ("reduces anxiety") pass the listing check and the publisher check.
3. "stress relief" is treated differently from stage to stage.
4. QA never checks printed coloring, sticker or activity files for medical wording.
5. Botsy can't make most of the lifestyle logs you named yet. See section 6.

## 3. What the platforms say (sources in section 11)

- **Etsy:**
  - Listings "may describe general wellness benefits". They may not claim a product "prevents, heals, treats, diagnoses, mitigates, or cures a disease or medical condition".
  - A disclaimer does not fix a banned claim.
  - Seller reports (anecdotes, not policy) describe takedowns for words like "healing" and "anti-anxiety".
- **Amazon KDP:**
  - Zero tolerance for content meant to mislead, or content that doesn't accurately represent the book.
  - A+ Content claims about health products need a note that backs them up.
  - I found no KDP rule against health log books. Leaving them out is your choice, and it is stricter than KDP.
- **FTC (US):**
  - Its 2022 guidance covers all health-related claims.
  - Such claims need "competent and reliable scientific evidence", usually randomized controlled trials.
  - Soft words like "helps" or "may" are likely not enough.
  - Example: the FTC made Lumosity pay $2 million (2016) over unsupported brain-benefit claims. So "improves focus" is risky copy.
- **FDA:** its rules are for supplements and devices, not paper goods. I used them only to see where "disease" starts. "Promotes relaxation" or "stress management" counts as wellness. "Helps treat an anxiety disorder" does not.

What follows for Botsy:
- Naming a problem and promising a result ("relieves stress") is a health claim, and we have no evidence for it.
- Words for the look and feel ("relaxing", "calm", "cozy") make no promise and are fine.

## 4. The term policy

The matching works the way the current blocklist matching does:
- whole words or phrases
- plural and possessive forms included
- case ignored

### 4.1 Medical: left out on Etsy and KDP

These are hard blocks. A research row that contains one supports no theme or tag. No listing, printed page, file name, search list or KDP field may use one.

- **Vital signs and blood tests:** blood pressure, hypertension, blood sugar, blood glucose, glucose, diabetes, diabetic, insulin, a1c, cholesterol, oxygen level, spo2.
- **Medication:** medication, meds, prescription, pill tracker, pill log, pill organizer, pill reminder, dosage, dose log, dose tracker.
  - Not "medicine": TTRPG character sheets print a "Medicine" skill (D&D 5e, Pathfinder 2e, Call of Cthulhu in `config/ttrpg_systems.yaml`).
  - "medicine" stays banned in listing copy, as it is today.
- **Symptoms and illness:**
  - symptom, chronic illness, chronic pain, pain journal, pain log, pain tracker, pain diary, pain scale, pain management, back pain, joint pain
  - migraine, headache log, headache diary, headache tracker, flare tracker, spoonie, fever log, fever tracker
  - fibromyalgia, arthritis, ibs, crohn's, colitis, celiac, allergy log, allergy tracker, asthma, epilepsy, seizure
  - cancer journal, cancer patient, breast cancer, chemo, chemotherapy
  - dementia, alzheimer's, parkinson's, lupus, endometriosis, pcos, pmdd, thyroid, dialysis, heart disease, stroke recovery, copd, long covid
  - acne, eczema, psoriasis, insomnia, sleep apnea
- **Clinical care:** medical history, doctor appointment log, vaccination record, immunization record, surgery recovery, post-op, physical therapy, occupational therapy, speech therapy, caregiver log, patient log.
- **Mental illness and clinical worksheets:**
  - depression, depressive, bipolar, ptsd, ocd, panic attack, panic disorder, anxiety disorder, mental illness
  - eating disorder, anorexia, bulimia, binge eating, self-harm, suicide, suicidal, schizophrenia, psychiatric
  - cbt, dbt, cognitive behavioral, dialectical behavior, emdr, therapy worksheet, counseling worksheet
- **Pregnancy and fertility medicine:** fertility, ovulation, basal body temperature, bbt, ivf, ttc, trying to conceive, contraception, birth control, kick count, contraction timer, contraction log, prenatal, miscarriage.
- **Weight-loss drugs and surgery:** bariatric, ozempic, semaglutide, wegovy, mounjaro, glp-1.
- **Food diaries:** food diary, food journal, food log. The proposal question you answered listed "food diary" as a health log book. Meal planners, grocery lists and recipe pages stay fine.

Side effect: this also leaves out disease-awareness designs, such as pink-ribbon breast cancer stickers or diabetes awareness stickers. Question 4 lets you bring them back as review items.

### 4.2 Borderline: you review first

These are not blocked. An item that contains one waits in `output/review/` for your approval code. Hermes never approves alone. Hermes never makes more of a borderline product on its own.

- **Periods:** period tracker, period log, period diary, menstrual, cycle tracker, menopause, perimenopause.
- **Pregnancy and babies:** pregnancy, baby log, newborn log, feeding log, breastfeeding, pumping log. Anything with kick counts or contractions is medical (4.1).
- **Weight:** weight loss, weight tracker, calorie, macro tracker, intermittent fasting.
- **Sobriety and recovery:** sobriety, sober, alcohol free, quit smoking, recovery journal, addiction, rehab.
- **Grief:** grief, bereavement.
- **General health:** health journal, health log, health tracker, health planner, health record.
- **Stress and mental load:** stress management, burnout, dopamine (as in "dopamine menu"), trauma.
- **Supplements:** vitamin, supplement, detox. "Digital detox" is a safe phrase and stays fine.
- **Diagnoses and care roles:** autism, autistic, dyslexia, special needs, counselor, counseling, psychologist, psychiatrist.

What you check: the copy describes only the layout, and the product is not really a medical log.

### 4.3 Lifestyle: fine (Etsy and KDP)

This list is for docs and tests. Botsy doesn't need an allow-list, because anything not in 4.1, 4.2 or 4.4 is allowed.

- Meditation, mindfulness, breathwork, yoga, pilates, stretching.
- Stress, as the topic of a layout: stress journal, stress log, stress check-in, stress level. Also worry journal.
- Mood: mood tracker, mood and energy log. For kids: feelings chart, emotions chart, calm-down corner.
- Gratitude, affirmations, journaling prompts, self-care, wellness, wellbeing, healthy habits.
- Habits and routines: habit tracker, morning routine, bedtime routine, screen time, digital detox.
- Water intake, sleep log (but "insomnia" is medical).
- Exercise: exercise log, workout log, fitness planner, gym log, running log, walking log, step tracker, cycling log, hiking log, strength training, heart-rate column.
- Food: meal planner, meal prep, grocery list, recipe pages; diet styles such as keto, vegan, low carb.
- Skincare routine (but acne, eczema and psoriasis are medical).
- ADHD-friendly, neurodivergent, neurodiverse, neurospicy. These are layout and audience words. The stationery generator already maps them to its ADHD-friendly style (`generators/stationery/styles.py`, about line 102).
- Look-and-feel words: relaxing, calm, calming, peaceful, cozy, gentle, quiet, zen, mandala.
- Pretend-play professions: doctor, nurse and vet pages in a kids' community-helpers pack.

### 4.4 Words never used in copy or on printed pages (the claims rule)

This applies to:
- title, tags, description and alt text
- printed pages, PDF properties and file names
- image prompts
- KDP title, subtitle, description, keywords and A+ images

The groups:
1. **Today's claim words stay as they are:** treat, cure, heal, diagnose, therapy, therapist, therapeutic, clinical, medical, medication, medicine, prescription, symptom relief, pain relief, anxiety relief, and their word forms. The safe phrases ("trick or treat") stay too.
2. **Condition words that are never written:** anxiety, anxious, mental health. The stationery renderer already refuses them; now every stage does. Research cuts them from theme names, and the row still counts toward its other themes.
3. **Result claims, built from three short lists:**
   - Effect word, then optionally your/the/my/their, then a target. Examples: "relieves stress", "reduce anxiety", "helps manage stress", "eases tension", "calm your nerves", "improves sleep", "boosts your mood", "regulate your emotions", "lowers blood pressure", "manage your ADHD", "improve your focus", "helps with ADHD".
     - Effect words: relieve, reduce, lower, ease, soothe, calm, alleviate, manage, control, fight, beat, overcome, combat, prevent, regulate, banish, improve, boost, sharpen, enhance, "help with". Each also matches its -s and -ing forms.
     - Targets: stress, anxiety, worry, tension, nerves, panic, overwhelm, burnout, depression, pain, symptoms, insomnia, sleep, blood pressure, blood sugar, cortisol, cravings, mood, emotions, mental health, wellbeing, ADHD, focus, attention, concentration, memory, brain fog.
   - Target, then a result noun: stress relief, stress reliever, stress buster, mood booster, anxiety reducer, stress reduction.
   - Fixed phrases: anti-anxiety, anti-stress, antidepressant, for anxiety, for depression, for insomnia, for pain, for symptoms, for stress relief, lose weight, burn fat.
   - Proof and authority claims: clinically proven, scientifically proven, proven to, doctor recommended, doctor approved, therapist approved, therapist designed, psychologist designed, evidence-based, research-backed, science-backed, FDA approved.
4. **No "not medical advice" line.** Etsy says disclaimers don't fix a claim, and the line adds a health angle.

Why word order matters: a rule that matched words "within 3 words, either order" would block normal copy. I tested it: "ADHD planner to manage your day", "Mood tracker to manage your month" and "improve your weight lifting" were all blocked. The word-order phrases above leave these alone. I tested them with Botsy's existing matcher: 18 claim examples were blocked and 17 layout examples passed. "weight" is left out of the targets on purpose; "lose weight" covers the claim.

Allowed, because it describes the layout or the look:
- "Stress journal with daily check-in pages"
- "Rate your stress from 1 to 5"
- "Rate your mood and energy each day and spot the patterns" (already in the stationery catalog)
- "Workout log with sets, reps and weight columns"
- "Sleep log: bedtime, wake time and a notes box"
- "ADHD-friendly daily planner: big boxes, visual time blocks"
- "ADHD planner to manage your day"
- "helps you focus on your top 3 tasks"
- "Relaxing mandala coloring pages", "calming ocean scenes"
- "manage your week", "reduce clutter"

### 4.5 Words matched only inside phrases (false-positive guards)

These words have common non-medical uses, so they never match on their own:
- health: the TTRPG "Health" field, "healthy habits"
- pain: "no pain no gain"
- pill: "pill bug"
- cancer: the zodiac sign
- stroke: "brush stroke"
- contraction: grammar worksheets
- period: the teacher planner prints "Period" labels (`generators/stationery/pages.py` line 146), "pay period"
- sugar: "sugar cookie"
- pressure, dose, refill, withdrawal, panic, recovery, temperature, cycle
- fever: "spring fever", "cabin fever"
- medicine, psychology, sanity, wound, dying, first aid: TTRPG sheets print these

I checked the term lists above against all 4,757 strings in `generators/` and `config/ttrpg_systems.yaml`. The only hit was "neurodivergent", which is now treated like ADHD.

### 4.6 How "stress relief coloring pages" is handled

This is the default. Question 2 lets you change it.

- **Research:**
  - "stress relief" is cut from themes, the way "anxiety relief" is cut today.
  - The row still counts as coloring-page demand for its design themes, such as "mandala" or "ocean animals".
- **Titles and printed pages:** never. For example, use "Relaxing Mandala Coloring Pages for Adults, 20 Pages".
- **Tags:** never. These alternatives fit Etsy's 20-character limit:
  - "relaxing coloring" (17)
  - "mindful coloring" (16)
  - "adult coloring pages" (20)
  - "calm coloring pages" (19)
  - "mandala coloring" (16)
- **Why:**
  - It names a problem and promises a result.
  - We have no proof of that result.
  - Every stage now agrees with what the stationery renderer already does.
- **Trade-off:**
  - It is a popular search, so some Etsy traffic may be lost. How much is unknown (unverified).
  - Etsy probably allows "stress relief" as a general wellness benefit. That is also unverified: no Etsy example names it.

### 4.7 ADHD (unchanged)

- "ADHD-friendly", "ADHD planner" and "for ADHD brains" stay allowed as layout and audience words.
- ADHD plus a medical word ("ADHD medication tracker") is medical.
- ADHD plus a result claim ("manage your ADHD", "helps with ADHD") is blocked.

## 5. How it plugs in

- **One new file:** `config/health_terms.txt`.
  - It uses the blocklist's own format: a plain line is medical (4.1), and a line starting with `?` is borderline (4.2).
  - A header cites CLAUDE.md and your decision of 2026-10-16.
- **One loader change** in `core/compliance.py`: `Blocklist.load` also reads this file.
  - All 14 places that load the blocklist pick it up at once: research, the briefs guard, the generators, QA, listing, publisher, the search list and the pipeline.
  - The Blocklist remembers which terms came from the health file, so messages say "health topic" and the search list says "(check: health?)", not "(check: trademark?)".
- **What each stage then does, using code it already has:**
  - Research: a medical row is marked blocked and supports no theme or tag. A theme where 50% or more of the rows are medical is dropped. At 20% or more it is flagged. These are the existing `blocked_cooccurrence` settings, 0.5 and 0.2.
  - Briefs: a brief that holds a medical term is refused (`core/briefs.py`).
  - Generators: the stationery and TTRPG renderers already refuse blocked terms.
  - QA: a medical term in a finished PDF or ZIP is an error. A borderline term is a warning, which sends the item to review.
  - Listing: a medical term in the item data stops the listing before any API call. In model-written copy it gets repaired. A borderline term sends the item to review.
  - Publisher: it refuses medical terms. Borderline items need your approval.
  - Search lists: the Etsy weekly list now, and the KDP list later, drop medical phrases and mark borderline ones.
- **Claims (4.4):** one small new module, `core/health.py`, holds the three short lists and builds the phrases.
  - The listing claim words get all of them.
  - Research gets the short noun forms ("stress relief", "mood booster", "anti anxiety") and the condition words (anxiety, anxious, mental health).
  - The existing test keeps research's list inside listing's list.
  - Match the phrases with one combined regex. About 8,000 phrases checked one at a time took 0.3 s for 40 texts, which works but is slower than it needs to be.
- **Printed pages:**
  - The stationery renderer adds the claims check and keeps its stricter extras.
  - The TTRPG renderer gets medical topics through the blocklist. It gets no new word list, so game words still print.
  - QA runs `listing.rules.medical` on printed text for coloring, stickers and activity sheets. For stationery and TTRPG it uses each generator's own `text_problems`.
- **Settings:** one key, `compliance.health_terms: config/health_terms.txt`. No word lists go in settings.
- **SNN and Hermes:** whatever they suggest becomes a theme or brief and passes the same checks. Hermes may make more of a winning lifestyle product within limits.
- **Cost:**
  - The checks are local text matching, with no API spend.
  - A borderline item costs one listing-copy call before you review it: a few cents (estimate, unverified).
  - Stationery pages are built by code, with no image cost.
- **Platform rules:** nothing here adds any new automated access to any site.

## 6. Making the lifestyle logs (follow-up, separate from the policy)

You named meditation, stress and exercise logs. Botsy can't make most of them yet:
- **Research:** the stationery niche rules (`research/niches.py`) don't recognize these titles. I tested "Workout Log", "Exercise Log", "Stress Journal", "Yoga Journal", "Running Log" and "Water Intake Tracker": none landed in a niche. "Meditation Journal Printable", "Sleep Log" and "Fitness Tracker" did.
  - Fix: add patterns for meditation, mindfulness, stress, workout, exercise, gym, running, yoga, water, sleep and self-care, each followed by log, journal, tracker or planner. Never add medical words.
- **Generator:** the stationery catalog has habit, mood and energy, meal and gratitude pages. It has no meditation, stress, workout, sleep or water pages.
  - Fix: the KDP plan already calls for a generic "field log" page type (`docs/KDP_RESEARCH.md`, fishing log). Reuse it with lifestyle columns:
    - meditation: date, minutes, type, notes
    - workout: exercise, sets, reps, weight
    - stress check-in: level 1-5, what helped
    - sleep: bedtime, wake time, notes
    - water: 8 cups
  - These pages are built by code, with no image cost.
- Suggested timing: after the packs build lands.

## 7. Repo changes, in order (after the running build lands)

1. `config/health_terms.txt` (new) and the loader change in `core/compliance.py`, plus the settings key.
2. `core/health.py` (new), with the claim lists. Add them to the research and listing `medical_terms`.
3. The listing prompt rule in `listing/model.py`, and the claims check in the stationery renderer.
4. QA's printed-text check for each niche.
5. The search-list note for health terms.
6. Tests (section 8).
7. Docs: `docs/PACKS.md` line 77, `docs/KDP.md` line 34, `docs/KDP_RESEARCH.md` line 118, and mark `docs/PROPOSAL_2026-10-16.md` line 30 as answered.
8. The CLAUDE.md line (section 9), after you confirm it.
9. `task_state.md`.
10. Later: the lifestyle log follow-up (section 6).

## 8. Tests to add

- **Medical, blocked:**
  - "Blood Pressure Log Book", "Blood Sugar Tracker", "Diabetes Log", "Medication Tracker", "Pill Log", "Symptom Diary"
  - "Migraine Journal", "Chronic Pain Tracker", "CBT Worksheet", "DBT Skills Workbook"
  - "Fertility Tracker", "Kick Count Log", "Eating Disorder Recovery Journal", "Food Diary"
  - "ADHD Medication Tracker", "Insomnia Sleep Diary"
- **Borderline, flagged:** "Period Tracker", "Pregnancy Journal", "Weight Loss Tracker", "Calorie Log", "Sobriety Tracker", "Grief Journal", "Health Journal", "Dopamine Menu", "Autism Visual Schedule".
- **Lifestyle, clean:**
  - "Meditation Journal", "Stress Journal with Daily Check-In Pages", "Workout Log", "Running Log", "Yoga Journal"
  - "Gratitude Journal", "Habit Tracker", "Water Intake Tracker", "Mood Tracker", "Sleep Log", "Meal Planner"
  - "ADHD Daily Planner", "Neurodivergent Planner", "Digital Detox Challenge", "Relaxing Mandala Coloring Pages"
  - "Workout Log with Heart Rate"
- **False-positive guards, clean:**
  - "Cancer Zodiac Stickers", "Contractions Worksheet", "Pill Bug Coloring Page", "Brush Stroke Practice"
  - "Pay Period Budget", "Class Schedule, Period 1-8", "Planner Refills", "No Pain No Gain Workout Log"
  - "Sugar Cookie Coloring Page", "Spring Fever Stickers", "Trick or Treat Coloring Pages", "Doctor Pretend Play"
- **Printed-string sweep:** every string in `config/ttrpg_systems.yaml` and the stationery catalog passes the health topics. This guards "Medicine (Wis)", "Psychology", "Sanity", "Major Wound" and "Period".
- **Claims, blocked:**
  - "Stress Relief Coloring Pages", "relieves stress", "reduces anxiety", "helps manage stress", "eases tension"
  - "improves sleep", "boosts your mood", "improve your focus", "anti-anxiety journal", "clinically proven"
  - "lowers blood pressure", "Manage your ADHD with this planner", "coloring for anxiety", "mood booster stickers"
  - "reduce stress journal" (this guards against safe phrases hiding a claim)
- **Claims, allowed:** "rate your stress from 1 to 5", "calm, detailed patterns", "ADHD planner to manage your day", "Mood tracker to manage your month", "manage your week", "helps you focus on your top 3 tasks", "Workout log with sets, reps and weight columns".
- **Floor:** the test fails if core medical terms (blood pressure, blood sugar, medication, symptom, cbt) are removed from `config/health_terms.txt`. This replaces a second word list kept in code.
- **Research:**
  - 12 rows "Health Planner Printable, Medication Tracker, Blood Pressure Log" from 3 shops give no theme. A "planner" theme built from them is dropped.
  - 12 rows "Stress Relief Coloring Pages, Mandala" give a "mandala" theme, and no "stress" or "relief" anywhere in the brief.
- **Listing:** a model title "Stress Relief Coloring Pages" gets repaired. A borderline theme sends the item to review as a "health topic".
- **QA:** a coloring PDF whose cover prints "Stress Relief" fails. A 5e sheet that prints "Medicine (Wis)" passes.
- **Publisher:** "reduces anxiety" is refused.
- **Search list:** "blood pressure log" is dropped, and "period tracker" is marked "(check: health?)".

## 9. CLAUDE.md

CLAUDE.md says to ask before changing compliance rules. You decided the policy, so this line records it. Your answer was to a KDP question, so the line also applies it to Etsy. Please confirm.

Proposed line, under "No medical or therapeutic claims":

"- Health topics (user, 2026-10-16): no medical products or themes on any platform (e.g. blood pressure, blood sugar, medication, symptom or therapy logs). Lifestyle journals and logs are fine (e.g. meditation, stress, exercise, mood, sleep), described only by their layout and look, never by what they do for health. Terms live in `config/health_terms.txt`; borderline (`?`) terms go to human review."

## 10. Questions for you

Each one has a default. Botsy uses the default until you answer.
1. Add the CLAUDE.md line, covering Etsy as well as KDP? Default: yes.
2. "stress relief": keep it out everywhere (default), or allow it only as an Etsy tag or KDP keyword? Allowing it changes a compliance rule.
3. The borderline list (periods, pregnancy, weight loss, sobriety, grief, health journals, autism and others): send each item to your review (default), or leave them all out?
4. Three things are left out by default that you might want back as review items: anxiety and mental-health items, disease-awareness stickers, and food diaries. Default: leave them out.

## 11. How the sources were checked

- I used web search summaries only, and opened no pages. CLAUDE.md allows automated access only to sites recorded in `config/platform_policies.yaml`, and Etsy and Amazon are recorded as forbidden.
- The draft's author made one direct fetch of an Etsy policy page by mistake. Etsy refused it (HTTP 403), and the author did not try again.
- Read the live pages yourself before relying on exact wording. The URLs are in `sources`.
