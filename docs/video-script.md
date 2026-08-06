# Demo script

Six beats, 90 seconds. Record after the other ten projects are ready so the
setup and pacing stay consistent across the set.

Warm both the Render service and the Neon database immediately before recording.
Free tiers suspend when idle, and a 30 second cold start on camera reads as
"broken" rather than "free".

---

**1. The problem (10s)**
"Prompt changes ship on vibes. Three manual spot checks look fine, it merges, and
a slice of users quietly breaks. So I built the thing that decides whether an AI
change is allowed to ship."

**2. The gate blocking (20s)** — screen: terminal running `shipgate gate`
"Here's a change that makes the classifier worse. The gate scores it against the
last clean run on main, sees a 25 point drop, and exits non-zero."
Show the red check on the Actions run and the score-diff summary.

**3. Why per-slice matters (15s)** — screen: the summary table
"The overall number is not enough. A model can hold its headline score while one
language slice collapses. The guard fires per slice and names the one that broke,
so the message is actionable rather than just red."

**4. The judge, and why you cannot trust it yet (20s)** — screen: calibration table
"Grading open-ended output needs a model, and a model grading a model is a noisy
instrument. So I hand-labeled a hundred examples and measured agreement.
My first rubric scored a perfect kappa of 1.0. That was the bug. I had shown the
judge the expected answer, so it was doing string comparison. Removing it dropped
kappa to 0.86, and that number finally meant something."

**5. The number that changed the design (15s)** — screen: variance table
"Then I ran the judge five times on identical inputs. The score moved twenty
points. My threshold was two. So the judge does not gate: exact match does,
because it has zero variance, and the judge is advisory until I can afford a
steadier one."

**6. Close (10s)** — screen: dashboard
"Versioned datasets, three runner types, a calibrated judge, nightly drift
detection, and a gate any of my other projects adopts with two files. Runs
entirely on free tiers. Total infrastructure spend: zero."

---

## What to have open

1. Terminal, repo root, quota fresh
2. The Actions run with the red check and its job summary
3. `README.md` scrolled to the calibration table
4. The dashboard, already warmed

## What not to do

Do not claim the judge is reliable. The interesting story is that it was measured
and found too noisy to gate on, and that finding is stronger than a clean number
would have been.
