# Phase 4 Scoping: Cross-Repo Architecture Decision

Companion to `docs/REPO_RESTRUCTURING_PLAN.md` §3's Phase 4 — that
section named three options (monorepo merge / git submodule / automated
one-way sync) at a high level and deliberately didn't spec them further,
since this decision needs Jon, not an agent. This document does the
actual scoping: real data, concrete step-by-step plans, and an honestly
reconsidered recommendation (it differs from the original plan doc's
lean — see "What changed my mind" below).

Written 2026-09-16, after Phase 1-3 were completed and verified.

---

## 1. Real data, checked directly (not assumed)

| | `shadow-man-remastered-randomizer` | `shadow-man-remastered-ap-world` |
|---|---|---|
| Stars | 0 | 0 |
| Forks | 0 | 0 |
| Watchers/subscribers | 0 | 0 |
| Open issues | 0 | 0 |
| Published releases | v1.0.0 → v1.2.0 (11 releases) | v0.1.0, v0.1.1 (v0.1.2 pending) |

**Zero community engagement on GitHub itself, on either repo.** This is
a real, checked fact, not an assumption — the original plan doc's
caution about "players/contributors have bookmarked/starred/cloned it"
(§3, Phase 4 option (a)) doesn't hold up against the actual numbers.
Whatever repo-shape decision gets made, migration cost from *broken
stars/forks/watching* is currently zero.

**But this is not the same as zero risk.** Real GitHub Releases exist
with real download-asset URLs (`shadowman.apworld`,
`shadow_man_ap_companion.exe`, the standalone exe) — these may already
be shared with real players outside GitHub's own visible metrics (e.g.
a Discord message, per this project's own README pointing people at
Discord: `bropacman`). GitHub release asset URLs survive a repo
**rename** (redirects) but do **not** survive a repo being deleted or
made private. Any option below that would delete/hide
`shadow-man-remastered-ap-world` needs to either keep it around (even
empty/archived, pointing at the new location) or accept that old
release links break.

**A weaker consideration than originally thought — official Archipelago
submission.** The original plan doc didn't raise this, but it's worth
naming and then setting aside: submitting a world for inclusion in
`ArchipelagoMW/Archipelago` itself is done via a PR that adds
`worlds/<game>/` to *their* repo — copying files in, not attaching your
dev repo. This works identically regardless of whether your own dev
repo is a monorepo or a dedicated one (`git subtree split` or a plain
copy both work fine either way). **This is not a reason to prefer
keeping repos separate** — noting it here only because it's the kind of
argument that sounds compelling until checked.

---

## 2. The three options, scoped concretely

### Option (a): Monorepo merge

**What actually happens:**
1. Pick a layout inside `shadow-man-remastered-randomizer` — e.g.
   `apworld/` for what's currently `worlds/shadowman/`'s own-only files
   (`__init__.py`, `options.py`, `regions.py`, `access_rules.py`,
   `items.py`, `locations.py`, `client.py`), with the already-shared
   "canonical" files (`fill.py`, `constants.py`, `access_rules.py`'s
   core, etc.) either staying at repo root (imported by both sides) or
   moved into a genuinely shared `core/` package that both `apworld/`
   and the standalone modules import from.
2. `git subtree add` (or a plain history-preserving merge) the AP world
   repo's commits into the new `apworld/` subfolder, so its own commit
   history isn't lost.
3. Rewrite `build_apworld.py` — currently assumes a *second checkout*
   as `--source`; would instead just zip a subfolder of the same repo.
   Simpler, not harder.
4. Rewrite CI — the `apworld-drift-check` and `dll-rebuild-check`
   jobs currently checkout a *second* repo
   (`bropacman/shadow-man-remastered-ap-world`) specifically to compare
   against. In a monorepo, `tools/check_apworld_sync.py`'s entire
   reason to exist disappears — there's nothing left to drift, since
   editing shared logic and the AP-specific consumer happens in the
   same commit, same PR, same review. This is the biggest real win:
   **1b (the drift risk that started this whole investigation) is
   eliminated by construction, not just monitored.**
5. `shadow-man-remastered-ap-world` repo: either archive it with a
   README pointing at the new location (keeps old release asset URLs
   alive), or leave it as a frozen historical snapshot. Don't delete it
   outright — costs nothing to keep, and release links depend on it
   existing.
6. Update `RELEASING.md`, `release_apworld.py` (become much simpler —
   no more cross-repo sync/copy step at all), and both `.github/
   workflows/ci.yml` files (collapse into one).

**Effort**: medium — mostly mechanical (moving files, rewriting 2
scripts and 1 CI config), the real work is deciding the exact folder
layout and doing the `git subtree` history-preserving merge carefully.
Half a day to a day of focused work, most of it verification (re-run
every build script, every CI job, confirm nothing silently broke).

**Risk**: low, given zero external engagement. The main risk is
process risk during the merge itself (a botched history merge, or a
build script rewrite that silently breaks a build) — mitigated by
doing it as its own PR/commit series with the existing CI (rewritten
first, tested against the OLD two-repo layout) validating each step.

**What this does NOT fix**: nothing — this is the option that most
directly and permanently closes 1b/1c from the original plan doc.

### Option (b): Git submodule

**What actually happens:**
1. Decide which repo is the "parent" (probably
   `shadow-man-remastered-randomizer`, since it already contains
   `ap_patcher.py` and everything else the AP world depends on at
   apply-time).
2. Add `shadow-man-remastered-ap-world` as a submodule at e.g.
   `apworld/` in the parent repo. The submodule keeps its own separate
   git history/remote/identity.
3. `build_apworld.py`/CI/`release_apworld.py` point at the submodule
   path instead of a sibling checkout — smaller change than option (a)
   since the AP world's files are still logically separate, just
   nested via `.gitmodules` instead of copy-pasted.
4. Ongoing: any change to a "shared" file (the ~15 hand-copied ones)
   still needs a human decision about which side to edit and whether
   the other needs the same fix — submodules don't eliminate 1b, they
   just make the two sides' relationship *explicit and versioned*
   instead of implicit and copy-pasted. The drift risk moves from
   "did I forget to copy this file" to "did I forget to update the
   submodule pointer commit" — a different flavor of the same problem,
   not a smaller one.

**Effort**: low-medium — smaller mechanical change than (a), but the
day-to-day workflow gets *more* fiddly, not less: submodules require
remembering to `git submodule update`, commit the pointer bump as its
own step, and avoid the classic "detached HEAD inside the submodule"
trap. This is real, recurring friction for a solo maintainer, not a
one-time cost.

**Risk**: low upfront, but submodule workflow mistakes are a
well-known, extremely common source of confusion even for experienced
git users — arguably a worse ongoing risk profile than either extreme
(fully merged or cleanly separate with a good drift-check).

**What this does NOT fix**: 1b's underlying cause (two places to edit
shared logic) — only makes the relationship between them explicit.

### Option (c): Keep repos separate, formalize the sync (status quo, hardened)

**This is already substantially what exists today**, after Phases
1-3: `tools/check_apworld_sync.py` runs as a blocking CI gate,
`release_apworld.py` automates the data-file sync + regeneration +
verify + build sequence. What's *not yet* automated: the ~15
"near-identical"/"expected-divergent" hand-copied files still need a
human to notice a shared-logic change and manually port it to the
other repo — the drift-check only catches this *after the fact*, on
the next CI run or release, not at edit time.

**What "finishing" this option would look like:**
1. A `sync_to_apworld.py` script (named in the original plan doc) that
   becomes the *only* sanctioned way changes to the ~15 shared files
   ever move standalone → AP world — copies the files, doesn't just
   check them.
2. Either a human runs it before every AP-world-affecting commit, or
   CI runs it automatically and opens a PR against the AP world repo
   when the standalone repo's shared files change (more automation,
   more moving parts — a GitHub Actions workflow with write access to
   a *second* repo, which is a bigger permissions/security surface
   than anything in this project's CI so far).
3. Both repos keep their separate identities, histories, release
   cadences, and (if it ever matters) separate audiences.

**Effort**: low for the manual-script version, medium-high for the
CI-auto-PR version (cross-repo write permissions, a new failure mode
to design for: what happens if the auto-PR conflicts).

**Risk**: lowest one-time migration cost of the three, since nothing
about the current two-repo shape changes. Ongoing risk is unchanged
from today — the drift-check is a safety net, not a fix, and depends
on someone actually reading CI output before it becomes someone else's
problem down the line.

**What this does NOT fix**: 1b's root cause at all — it's the option
that treats the symptom (undetected drift) rather than the cause (two
places for one piece of logic to live).

---

## 3. Comparison

| | (a) Monorepo | (b) Submodule | (c) Formalized sync |
|---|---|---|---|
| Eliminates 1b's root cause | **Yes** | No (relocates it) | No (only detects it) |
| One-time migration effort | Medium | Low-medium | Low (mostly done already) |
| Ongoing day-to-day friction | Lowest (one repo, one PR per change) | **Highest** (submodule pointer discipline) | Medium (still 2 repos to think about) |
| Preserves separate repo identity/URL | No (AP world repo archived) | Yes | Yes |
| Risk from external stars/forks/issues | None (zero on both, checked) | None | None |
| Risk to already-shipped release links | Low if archived-not-deleted | None | None |
| CI complexity | Lowest after (collapses to 1 workflow) | Medium | Medium (current state) |

---

## 4. What changed my mind from the original plan doc

The original `REPO_RESTRUCTURING_PLAN.md` §3 leaned toward option (c)
specifically because of an *assumed* cost to (a) — "players/
contributors have bookmarked/starred/cloned it" — that turned out not
to be true (checked: 0 stars, 0 forks, 0 watchers, 0 issues, on both
repos). With that cost removed, (a) is the only option that actually
fixes the problem this whole investigation started from (1b, silent
cross-repo drift) rather than continuing to monitor it. (b) looks worse
the more concretely it's scoped — it trades an already-partially-solved
problem (drift, now caught by CI) for a well-known, ongoing git
workflow footgun.

**Revised recommendation: (a), if there's no reason to keep the two
repos' identities separate that I don't have visibility into.**

---

## 5. Questions only Jon can answer — please pick before anyone (agent or
otherwise) touches Phase 4

1. **Do you want `shadow-man-remastered-ap-world` to keep existing as
   its own visible, independent repo** — for reasons beyond current
   stars/forks (e.g. "AP players should be able to find and star just
   the AP world without wading through standalone-randomizer code," or
   "I like keeping the two audiences' issue trackers separate," or
   simply "I'm not ready to commit to merging them, even with today's
   numbers")? If yes, that alone rules out (a) regardless of the rest
   of this document's analysis — this is a product/identity call, not
   a technical one, and it's entirely yours to make.
2. If merging is acceptable: **do you want the `apworld/` subfolder
   layout, or would you rather see a bigger reorganization** (e.g. a
   genuinely shared `core/` package imported by both `apworld/` and
   the standalone modules, instead of the current "duplicate + drift-
   check" model even for the "expected-divergent" files like
   `access_rules.py`/`fill.py`)? The scoping above assumes the smaller,
   lower-risk version (merge folders, keep the existing duplicate-file
   pattern, just in one repo) — a deeper shared-core refactor is a
   much bigger, separate effort worth its own scoping if wanted.
3. **Timing**: do this now (while both repos are genuinely at zero
   external engagement, the cheapest this migration will ever be) or
   defer until after the AP world has had a real release cycle or two
   in the wild, to see whether a separate identity turns out to matter
   in practice?
