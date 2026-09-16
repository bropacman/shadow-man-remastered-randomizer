# Repo Restructuring & Formalization Plan

Status: **draft — not started.** This is a planning document only. Nothing
in this file has been executed. Written 2026-09-15 after cutting the
AP world v0.1.2 / Companion v0.1.2 release, which surfaced most of the
problems below directly (not hypothetically — see "Evidence" under each
item).

Goal of this document: give a future session (human or Claude agent)
enough context to pick up any one phase below independently, without
having to re-derive the reasoning from scratch or re-discover the risks
by hitting them again.

---

## 0. Current state (as of the v0.1.2 release)

Three repos are involved, only two of which are under version control here:

| Repo | Location | Role |
|---|---|---|
| `shadow-man-remastered-randomizer` | `C:\Users\jonat\Documents\shadow-man-remastered-randomizer` | Standalone single-player randomizer source, **and** the sole source for AP-facing patch application (`ap_patcher.py`, `apply_ap_seed.py`, `ap_gui.py`/AP Companion, `unique_retractor_keys_patch.py`, every EXE-patch module). |
| `shadow-man-remastered-ap-world` | `C:\Users\jonat\Documents\Archipelago-0.6.7\worlds\shadowman` (a git repo *nested inside* a local Archipelago install — easy to miss, see "Evidence" below) | Jon's own repo (GitHub: `bropacman/shadow-man-remastered-ap-world`, confirmed via `gh`). The actual Archipelago world plugin: `__init__.py`, `options.py`, `regions.py`, `access_rules.py`, `items.py`, `locations.py`, `client.py`, and hand-copied duplicates of ~15 patch/data modules from the repo above. |
| `Archipelago-0.6.7` (the enclosing folder) | `C:\Users\jonat\Documents\Archipelago-0.6.7` | **Not one of Jon's repos** — it isn't a git repo at all at that level (confirmed: `git status` from this folder fails with "not a git repository"). It's a plain local install of the upstream Archipelago project, used as a dev/test harness. The AP world repo above just happens to live nested inside its `worlds/` folder, because that's where Archipelago expects a world plugin to be for local testing — that nesting is *why* it's easy to miss that `worlds/shadowman/` is its own real repo with its own remote. |

Two build outputs ship to players, both produced *from* the two repos
above but requiring neither repo to be cloned by a player:

- `shadowman.apworld` — packaged by `build_apworld.py` (lives in the
  standalone repo) from the AP world repo's source.
- `shadow_man_ap_companion.exe` — a PyInstaller build of `ap_gui.py`
  (standalone repo) that bundles `apply_ap_seed.py` → `ap_patcher.py`
  and everything *that* imports, so a player never needs Python or
  either repo installed.

## 1. Risks found this session (with evidence, not speculation)

### 1a. No CI on either repo
**Evidence:** checked `.github/workflows` in both repos directly — the
only workflow files present anywhere in the standalone repo's tree
belong to vendored third-party deps (`overlay_dll/build/_deps/imgui_src-src`,
`.../minhook_src-src`), not this project. The AP world repo has no
`.github` at all.

**Consequence, observed directly tonight:**
- The committed `ShadowManOverlay.dll` in the AP world repo was a stale
  **Debug** build (2.8MB, dynamically-linked runtime) instead of the
  **Release** build (820KB, statically-linked) that the source
  (`overlay_dll/src/*.cpp`, standalone repo) actually produces once
  built correctly. Nothing caught this until a manual byte-for-byte
  comparison against a fresh local build.
- `extracted_locations.py` (standalone repo) had drifted stale relative
  to `data/locations.csv` — a regeneration step (`tools/generate.py`)
  that's supposed to run "after any edit" per this repo's own `CLAUDE.md`
  had silently not been run. Caught by chance because this session
  happened to run the regenerator and diff the result.

### 1b. Two-repo, hand-copied-file split with only a manual drift check
**Evidence:** `tools/check_apworld_sync.py` (standalone repo) exists
specifically to catch this and is documented as such in `RELEASING.md`
and extensively in `CLAUDE.md`'s "Cross-Repo Drift & Port Review"
section — this is a known, already-named problem, not a new finding.
What's new: it's *advisory only*. Nothing forces it to run. `RELEASING.md`
says `build_apworld.bat` runs it as "a non-fatal pre-flight step" —
i.e. a human has to actually read the output before shipping.

**Consequence:** roughly a **month** of real, working AP-side feature
code (Unique Retractor Keys generation logic, a Cadeauxsanity rename,
barrel/cadeaux diversification fixes, a large `client.py` backlog) sat
uncommitted in the AP world repo's working tree with zero redundancy —
one disk failure away from being lost — until this session found and
pushed it. It was invisible to anyone (including future Claude
sessions) who only looked at the standalone repo or at `CLAUDE.md`,
since `CLAUDE.md`'s own last entry predates most of it.

### 1c. The AP world repo isn't self-sufficient
**Evidence:** `ap_patcher.py`'s Step 7 does `import unique_retractor_keys_patch`
and similar direct imports of modules that exist **only** in the
standalone repo (same pattern as the already-documented
`dark_engine_patch.py`/`entrance_randomizer.py`). `ap_patcher.py` itself
is never duplicated into the AP world repo at all.

**Consequence:** this is invisible to *players* (the Companion exe's
PyInstaller build bundles everything transitively, per `build_ap_gui.bat`'s
own comment about "re-invoking itself as `apply_ap_seed.py`"), but a
contributor — or Jon, on a fresh machine — who clones only
`shadow-man-remastered-ap-world` and tries to generate+apply a seed
from source will hit a missing-module error at apply time with no
indication *why*, unless they already know to also clone the standalone
repo alongside it.

### 1d. No committed, automated test coverage
**Evidence:** `tests/` (standalone repo) is fully covered by
`.gitignore` (`tests/`) — it's a local scratch folder, not a suite
anyone else can run. `tools/test_fill.py` and `tools/fill_stress_test.py`
exist and look like real verification scripts, but nothing runs them
automatically.

**Consequence:** every verification this session (compile checks, the
drift check, spot-checking a built `.apworld`'s zip contents, comparing
DLL hashes) was done by hand, once, right before shipping. That's the
same shape of process that let 1a's DLL staleness happen in the first
place — it depends entirely on someone remembering to check.

### 1e. Built binaries checked into git with no build provenance
**Evidence:** `ShadowManOverlay.dll` and `shadow_man_ap_companion.exe`
are binary artifacts; the DLL is committed directly into the AP world
repo, the companion exe isn't committed anywhere (built fresh each
release, per `RELEASING.md`) — inconsistent treatment between two build
outputs of the same overall release.

**Consequence:** the DLL has no way to prove which source commit it was
built from beyond "someone's local build directory at some point" — see
1a's staleness bug, which is a direct symptom of this.

### 1f. Documentation sprawl, and version-track/tag-namespace ambiguity
**Evidence:** both repos keep almost every `.md` file flush at repo
root (`CLAUDE.md`, `HANDOFF.md`, `RELEASING.md` in the standalone repo;
`AP_FEATURE_GAP.md`, `LIVE_MEMORY_TRACKING_NOTES.md`,
`SESSION_NOTES_2026-07-15.md` in the AP world repo). Only one `docs/`
folder exists per repo so far (`docs/TECHNICAL.md` standalone;
`docs/screenshots/` AP world), used inconsistently.

Separately: the standalone repo tags its own releases `v1.x.y`
(`v1.0.0`…`v1.2.0`), while the AP world repo tags itself independently
on its own `v0.x` track (`v0.1.0`, `v0.1.1`, now `v0.1.2`) matching
`COMPANION_VERSION` in `ap_gui.py` (which lives in the *standalone*
repo). This session added a third tag shape, `apworld-v0.1.2`, in the
standalone repo to mark "this is the source commit that produced
Companion v0.1.2" — a reasonable ad hoc fix, but not a documented
convention, so it'll need re-deriving again next release unless it's
written down somewhere durable.

**Consequence:** no functional bug yet, but growing inconsistency makes
it harder for a future session (or contributor) to know at a glance
"what does this tag mean, and in which repo."

### 1g. Unsigned Companion exe (already known, worth restating here)
**Evidence:** already a "Known Issues" bullet in every AP world release
so far — Windows Smart App Control blocks the unsigned DLL/exe on a
clean install. Not new, but it's an adoption-friction risk that a
restructuring pass could reasonably fold in a fix for (code signing) if
the group ever wants to invest in it.

---

## 2. Constraints to respect (don't break these while restructuring)

- **`CLAUDE.md` likely needs to stay at (or very near) each repo's
  root.** Claude Code auto-discovers project-level `CLAUDE.md` files by
  walking up from the working directory; moving it into `docs/` risks
  breaking that auto-load unless verified first. **Verify this before
  moving it, don't assume.**
- **`guide_en.md` (AP world repo) must stay at a path AP's own
  `WebWorld.tutorials` references by name** (`__init__.py`) — confirmed
  via `build_apworld.py`'s own exclude-list comments. Any doc-folder
  move needs a matching path update there, tested against a real
  generation run, not just moved and assumed to still work.
- **`README.md` conventionally stays at repo root** (GitHub renders it
  on the repo homepage from root only, not from `docs/`).
- **`data/` is excluded from the packaged `.apworld`** by
  `build_apworld.py`'s `EXCLUDE_DIRS` — any change to where `data/`
  lives needs that exclude list (and `tools/generate.py`'s `ROOT`/
  `CSV_PATH` constants, in *both* repos' own copies of that tool) kept
  in sync.
- Whatever changes here must not silently break `build_apworld.py`,
  `build_ap_gui.bat`, or `build.bat` for the standalone exe — these are
  the only three release pipelines that currently exist and all three
  need to keep working through any restructuring phase.

---

## 3. Proposed phases

Ordered by risk-reduction-per-effort, not strict dependency — 4 and 5
could run in parallel with 1–3 if someone wants to split work across
multiple agents.

### Phase 1 — CI safety net (do first; lowest risk, highest immediate value)
Directly prevents 1a and 1d from recurring, with no structural changes
to the repos themselves.

- Add `.github/workflows/ci.yml` to **both** repos:
  - `python -m py_compile` across every tracked `.py` file (catches
    syntax errors before they ship — trivial, would have caught nothing
    tonight since nothing was actually broken, but is the cheapest
    possible floor).
  - Standalone repo only: check out the AP world repo as a second
    checkout in the same job (public repo, no auth needed for a read)
    and run `tools/check_apworld_sync.py` against it; fail the build on
    any `!!` drift line. This turns 1b from "advisory" into "blocking."
  - Windows runner job: rebuild `overlay_dll` from source (cmake/MSVC,
    same as `overlay_dll`'s existing build setup) and byte-diff the
    result against whatever's committed in the AP world repo at that
    commit; fail if they differ. Directly prevents 1a's DLL-staleness
    class of bug specifically, not just generically.
  - Whatever's salvageable from `tools/test_fill.py` /
    `tools/fill_stress_test.py`, wired in as an actual CI step instead
    of a local-only script (addresses part of 1d).
- Decide (open question for Jon, see §4): does a failing CI check
  **block** merges (needs branch protection rules configured on GitHub,
  some solo-dev friction) or just **flag** (a badge/notification, no
  merge block)? Recommend starting with flag-only given this is a
  solo/small-team project, revisit later if it proves too easy to
  ignore.

### Phase 2 — Documentation consolidation
Addresses 1f. Do *after* Phase 1's CI exists, so a doc-move that
accidentally breaks a tool-referenced path (guide_en.md, CLAUDE.md,
generate.py paths) gets caught by CI instead of discovered at the next
release.

- Standalone repo: create `docs/dev/` (session-log-style material —
  `CLAUDE.md` *if* verified safe to move, `HANDOFF.md`) and `docs/release/`
  (`RELEASING.md`, this file). Leave `README.md` at root. Leave
  `docs/TECHNICAL.md` where it is or fold into `docs/dev/`.
- AP world repo: create `docs/dev/` for `AP_FEATURE_GAP.md`,
  `LIVE_MEMORY_TRACKING_NOTES.md`, `SESSION_NOTES_2026-07-15.md`. Leave
  `README.md` and `guide_en.md` at root (guide_en.md per the hard
  constraint in §2 unless that reference is updated and tested too).
- Write a one-paragraph `docs/README.md` (or a section in the main
  README) in each repo explaining the layout, so this doesn't need
  re-deriving again.

### Phase 3 — Formalize the release process
Addresses 1e and 1f's tag-namespace ambiguity. Turns tonight's ~15
manual tool calls (sync data, regenerate, drift-check, compile-check,
build apworld, build companion exe, tag both repos, draft release notes)
into a single reproducible script.

- Write `release_apworld.py` (standalone repo) that does, in order:
  sync `data/locations.csv` → AP world repo, run its `tools/generate.py`,
  run `check_apworld_sync.py` and abort on drift, run both `py_compile`
  sweeps, build the `.apworld`, build the Companion exe, and print a
  checklist of what still needs a human (tagging, writing release notes,
  actually publishing). Doesn't need to auto-commit/push/tag/publish —
  those stay deliberate human (or agent-with-confirmation) actions —
  but should remove the *manual, error-prone plumbing* between them.
- Document the tag convention formally in `RELEASING.md`: standalone
  exe → `vX.Y.Z` (standalone repo); AP world → `vX.Y.Z` (AP world repo,
  independent 0.x track, matches `COMPANION_VERSION`); source-marker
  tag in the standalone repo for "which commit produced this AP
  Companion version" → `apworld-vX.Y.Z` (the shape used tonight —
  formalize it, don't just leave it as one-off precedent).
- Optional, larger line item: investigate code-signing the Companion
  exe (addresses 1g). Real cost (a certificate), not just engineering
  time — flag as its own decision, don't bundle into this phase's scope
  by default.

### Phase 4 — Decide the long-term cross-repo architecture
Addresses 1b/1c at the root instead of just adding a safety net around
them (Phase 1 catches drift, but doesn't stop the *cause* of drift:
that a human has to remember to hand-copy ~15 files across two
checkouts on two different local paths). This is the biggest, riskiest,
and most optional phase — **don't start it without an explicit go from
Jon**, and probably don't hand it to an agent solo given the blast
radius (it touches how both repos' histories and CI relate to each
other). Three real options, not a recommendation to pick one yet:

- **(a) Monorepo merge.** Combine both into one repo (e.g. a
  `standalone/`, `apworld/`, and shared `core/` package split). Fully
  eliminates 1b/1c by construction — there's nothing left to drift.
  Highest one-time cost: rewrites both repos' history relationship,
  likely needs a redirect/archival notice on the old
  `shadow-man-remastered-ap-world` repo (players/contributors have
  bookmarked/starred/cloned it), and needs `build_apworld.py`'s whole
  packaging model rethought (it currently assumes two separate
  checkouts as input).
- **(b) Git submodule.** Make the AP world repo's shared files a
  submodule pointing into the standalone repo (or vice versa). Keeps
  both repos' separate identities/audiences intact, makes the
  dependency explicit and versioned instead of copy-pasted. Real
  submodule-workflow friction for day-to-day dev (submodules are a
  common source of "forgot to update the pointer" bugs — a different
  drift risk, not a strictly better one, just a differently-shaped one).
- **(c) Automated one-way sync + CI enforcement, repos stay separate.**
  Smallest change from today: keep the two repos exactly as they are,
  but replace "a human runs `tools/generate.py` in two places and eyeballs
  a diff tool" with an actual sync script (`sync_to_apworld.py`) that's
  the *only* sanctioned way changes ever move standalone → AP world, run
  either by a human before every AP-world-affecting commit or by CI
  opening an auto-PR against the AP world repo when the standalone repo's
  shared files change. Phase 1's CI drift-check becomes the backstop
  that catches it if that sync script is ever skipped.

**Recommendation if forced to pick one today:** (c), specifically
because it's the only option that doesn't require deciding right now
whether the two repos' separate public identities/audiences (standalone
randomizer users vs. AP players) are worth preserving — that's a
product decision, not just a technical one, and shouldn't get decided
implicitly by which refactor was easiest to script.

---

## 4. Open decisions — need Jon, not an agent, to pick

1. Branch-protection / CI-blocking vs. CI-flagging-only (Phase 1).
2. Whether `CLAUDE.md`'s root-level placement is actually harness-required
   (needs verifying, not assuming) before Phase 2 touches it.
3. Whether to invest in code-signing the Companion exe (Phase 3, cost
   money, not just engineering time).
4. Phase 4's architecture choice (a)/(b)/(c) — and whether Phase 4
   happens at all, or the two repos just stay as-is with Phases 1–3 as
   the full scope of "formalization."
5. Whether the AP world repo should get its own `CLAUDE.md`/session-log
   file (mirroring the standalone repo's practice) now that a month of
   its own work has proven capable of going undocumented — or whether
   session notes should live in one place covering both repos.

---

## 5. Suggested execution order for agent work

Each phase below is written to be handed to a separate Claude agent
session with just that phase's section of this doc as context, plus a
pointer to §2's constraints. Phase 4 should not be delegated without
Jon first picking (a)/(b)/(c) from §3.

1. Phase 1 (CI) — standalone repo first, then AP world repo. Verify
   each workflow actually runs (a real push/PR, not just a `yml` that
   looks right) before considering it done.
2. Phase 2 (docs) — after Phase 1 exists, so a broken reference gets
   caught automatically.
3. Phase 3 (release script) — depends on Phase 1's CI being trustworthy
   enough to lean on inside the release script itself.
4. Phase 4 — only after Jon picks an option; scope a fresh, dedicated
   plan for it at that point rather than trying to fully spec it here
   in advance of that decision.
