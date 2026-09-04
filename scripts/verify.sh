#!/usr/bin/env bash
# The exit code of this script is the result. There is no other summary worth reading.
#
# Every step either RUNS and passes, or FAILS. Nothing is skipped, including the steps that
# need something installed. A check that quietly stands down when its dependency is missing
# prints the same "ok" as one that ran and looked, and a week later the log cannot tell you
# which happened. Where a dependency is genuinely absent the failure names the command that
# installs it and says what the rest of the suite does not cover without it.
#
# Run it from a fresh clone as well as in place. A whole class of bug, a path resolved to a
# sibling directory being the usual one, only shows up outside the tree it was written in:
#
#   git clone . /tmp/nl && cd /tmp/nl && bash scripts/verify.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

PASS=0
FAIL=0
FAILED_STEPS=()

# The scratch directory is RELATIVE and inside the repo, gitignored, rather than an absolute
# mktemp path. Two reasons, both learned the hard way here. This script's output is pasted
# into the README, and an absolute /home/<user>/... path there is private and unportable. And
# a per-run temporary path appearing in tool output would change that output on every run for
# a reason having nothing to do with the code, which is exactly how a fingerprint-based
# sabotage harness gets its second gate for free.
TMP=".verify-tmp"
rm -rf "$TMP"
mkdir -p "$TMP"
trap 'rm -rf "$ROOT/.verify-tmp"' EXIT

# Keep .pyc files out of the tree so the privacy scan and the tracked-file digest see the
# repository as committed.
export PYTHONDONTWRITEBYTECODE=1

hr() { printf '%s\n' "------------------------------------------------------------------------"; }

step() {
  local title="$1"
  shift
  hr
  printf '%s\n' "$title"
  hr
  if "$@"; then
    PASS=$((PASS + 1))
    printf '\n[PASS] %s\n\n' "$title"
  else
    local code=$?
    FAIL=$((FAIL + 1))
    FAILED_STEPS+=("$title")
    printf '\n[FAIL] %s (exit %d)\n\n' "$title" "$code"
  fi
}

# ---------------------------------------------------------------- 1. preconditions

check_toolchain() {
  local bad=0
  local version
  version="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
  if [ -z "$version" ]; then
    echo "  FAIL  python3 is required and was not found."
    bad=1
  else
    local major minor
    major="${version%%.*}"
    minor="${version##*.}"
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 10 ]; }; then
      echo "  FAIL  python 3.10 or newer is required, found $version. The package uses"
      echo "        PEP 604 unions and the 3.9 parser rejects them at import."
      bad=1
    else
      echo "  ok    python $version"
    fi
  fi

  # No third-party Python dependency exists on purpose, and this asserts it rather than
  # assuming it. A pip install creeping into the package would make the fresh-clone run
  # depend on a network the rest of this script never touches.
  if [ -f requirements.txt ] || [ -f pyproject.toml ] || [ -f setup.py ]; then
    echo "  FAIL  a Python packaging file appeared. The engine is standard library only,"
    echo "        and the pages workflow installs nothing before running the tests."
    bad=1
  else
    echo "  ok    no Python dependency file, so the engine is standard library only"
  fi

  if command -v node >/dev/null 2>&1; then
    echo "  ok    node $(node --version), which drives the browser check"
  else
    echo "  FAIL  node is required for the browser stage. Install it with:"
    echo "          sudo apt-get install nodejs"
    echo "        Without it nothing loads docs/index.html. The unit tests import the engine"
    echo "        and never open the page, so an inline script that fails to parse would"
    echo "        leave every one of them green while the page rendered as an empty shell."
    bad=1
  fi

  local chrome=""
  for candidate in "${CHROME_PATH:-}" /usr/bin/google-chrome /usr/bin/google-chrome-stable \
                   /usr/bin/chromium /usr/bin/chromium-browser; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then chrome="$candidate"; break; fi
  done
  if [ -n "$chrome" ]; then
    echo "  ok    a Chrome binary is present at ${chrome}"
  else
    echo "  FAIL  no Chrome binary found. Install one with:"
    echo "          sudo apt-get install google-chrome-stable"
    echo "        or set CHROME_PATH. See the note above on why the browser stage is not"
    echo "        optional here."
    bad=1
  fi

  if git rev-parse --git-dir >/dev/null 2>&1; then
    echo "  ok    inside a git repository, so the privacy scan has a file list to work from"
  else
    echo "  FAIL  not a git repository. The privacy scan reads git ls-files and would pass"
    echo "        without opening a single file."
    bad=1
  fi
  return $bad
}

# ---------------------------------------------------------------- 2. unit tests

run_unit_tests() {
  # Quiet on success, everything on failure. A wall of dots in a passing log is noise, and
  # the whole run is what you want the moment one of them is an F.
  python3 -m unittest discover -s tests -t . -v > "$TMP/tests.txt" 2>&1
  local status=$?
  if [ "$status" -ne 0 ]; then
    cat "$TMP/tests.txt"
    return 1
  fi
  local count
  count="$(sed -n 's/^Ran \([0-9]*\) tests\? in .*/\1/p' "$TMP/tests.txt" | head -1)"
  if [ -z "$count" ] || [ "$count" -lt 50 ]; then
    echo "  FAIL  only ${count:-no} tests reported as run. Either discovery matched nothing"
    echo "        or unittest changed its output format and this count is not real."
    cat "$TMP/tests.txt" | tail -20
    return 1
  fi
  if ! grep -q '^OK' "$TMP/tests.txt"; then
    echo "  FAIL  unittest did not report OK"
    tail -30 "$TMP/tests.txt"
    return 1
  fi
  echo "  ok    $count tests passed"
  echo "$count" > "$TMP/test-count.txt"
  return 0
}

# ---------------------------------------------------------------- 3. the CLI end to end

run_cli() {
  local bad=0

  # The negative control at the command line. The corpus is split by
  # fixtures/expected/flags.json into labels that must raise a flag and labels that must
  # not, and BOTH halves are exercised. A checker that flags everything passes any suite
  # built only from broken labels, and it is worse than useless in an advisory tool.
  python3 - > "$TMP/split.sh" <<'PY'
import json
data = json.load(open("fixtures/expected/flags.json"))["expected"]
clean = sorted(k for k, v in data.items() if not v)
broken = sorted(k for k, v in data.items() if v)
print("CLEAN=(" + " ".join(f'"fixtures/labels/{k}.json"' for k in clean) + ")")
print("BROKEN=(" + " ".join(f'"fixtures/labels/{k}.json"' for k in broken) + ")")
PY
  # shellcheck disable=SC1091
  source "$TMP/split.sh" || return 1

  if [ "${#CLEAN[@]}" -lt 8 ] || [ "${#BROKEN[@]}" -lt 5 ]; then
    echo "  FAIL  the corpus split is ${#CLEAN[@]} clean and ${#BROKEN[@]} broken labels,"
    echo "        which is too few of one kind for either half to mean anything."
    return 1
  fi

  if python3 -m nutrilabel check "${CLEAN[@]}" > "$TMP/clean.txt" 2>&1; then
    echo "  ok    check exits 0 on all ${#CLEAN[@]} internally consistent labels"
  else
    echo "  FAIL  a label that is consistent with itself was flagged:"
    grep -F "[FLAG]" "$TMP/clean.txt" | head -5
    bad=1
  fi

  python3 -m nutrilabel check "${BROKEN[@]}" > "$TMP/broken.txt" 2>&1
  if [ $? -eq 1 ]; then
    echo "  ok    check exits 1 on all ${#BROKEN[@]} labels that contradict themselves"
  else
    echo "  FAIL  a label that contradicts itself did not fail the gate"
    bad=1
  fi
  local flagged
  flagged=$(grep -cF "[FLAG]" "$TMP/broken.txt")
  if [ "$flagged" -lt "${#BROKEN[@]}" ]; then
    echo "  FAIL  only $flagged flags across ${#BROKEN[@]} broken labels, so at least one"
    echo "        exited 1 on somebody else's problem"
    bad=1
  else
    echo "  ok    $flagged flags raised across ${#BROKEN[@]} broken labels"
  fi

  # Every basis must be reachable, and every figure must say which basis it is on. A
  # column of numbers with no basis stated is how the comparison goes wrong to begin with.
  for basis in per_serving per_100g per_100kcal per_container; do
    if python3 -m nutrilabel normalize fixtures/labels/fda-sample-panel.json \
        --basis "$basis" > "$TMP/basis.txt" 2>&1; then
      # normalize prints the label summary first and the basis line second, so both are
      # checked. The point is that the basis appears before any figure, not that it is on
      # any particular line.
      if head -2 "$TMP/basis.txt" | grep -qi "basis in force"; then
        echo "  ok    --basis $basis states the basis in force on its first line"
      else
        echo "  FAIL  --basis $basis printed figures without saying what they are per:"
        head -2 "$TMP/basis.txt"
        bad=1
      fi
    else
      echo "  FAIL  --basis $basis failed on the FDA sample panel:"
      tail -3 "$TMP/basis.txt"
      bad=1
    fi
  done

  # The trap the task is about, at the command line: two cereals that look far apart per
  # serving and overlap per 100 g.
  python3 -m nutrilabel compare fixtures/labels/cereal-a-flakes.json \
    fixtures/labels/cereal-b-granola.json --basis per_container > "$TMP/compare.txt" 2>&1 || {
      echo "  FAIL  compare failed on the two cereals"; tail -3 "$TMP/compare.txt"; bad=1; }
  if grep -q "compound" "$TMP/compare.txt"; then
    echo "  ok    the per-container comparison warns that two roundings compound"
  else
    echo "  FAIL  per_container produced no warning about compounding rounding, which is"
    echo "        the one thing a reader most needs told on that basis"
    bad=1
  fi

  # OCR must refuse rather than guess. This is stated in the task and is the difference
  # between a wrong number and a missing one.
  python3 -m nutrilabel ocr fixtures/ocr/smudged-panel.tsv > "$TMP/ocr.txt" 2>&1
  if grep -qi "refused" "$TMP/ocr.txt"; then
    echo "  ok    a smudged panel produces refusals rather than guessed digits"
  else
    echo "  FAIL  the smudged panel was read without refusing anything:"
    head -5 "$TMP/ocr.txt"
    bad=1
  fi
  if grep -qE "^\s*sodium\s+160" "$TMP/ocr.txt"; then
    echo "  FAIL  the token '16Omg', with a letter O in it, was corrected to 160 rather"
    echo "        than refused. A correction is a guess wearing the face of a reading."
    bad=1
  else
    echo "  ok    the ambiguous glyph in '16Omg' was refused, not corrected to 160"
  fi

  # Every rule must carry a citation, and every interval that rests on a guess must say so.
  python3 -m nutrilabel rules --format US_2016 > "$TMP/rules.txt" 2>&1 || {
    echo "  FAIL  rules failed for US_2016"; bad=1; }
  if grep -q "21 CFR 101.9" "$TMP/rules.txt"; then
    echo "  ok    the US rule table prints its citations"
  else
    echo "  FAIL  the US rule table printed no citation"
    bad=1
  fi
  python3 -m nutrilabel rules --format EU_1169 > "$TMP/rules-eu.txt" 2>&1 || {
    echo "  FAIL  rules failed for EU_1169"; bad=1; }
  if grep -qi "guidance" "$TMP/rules-eu.txt"; then
    echo "  ok    the EU rule table marks itself as resting on guidance, not statute"
  else
    echo "  FAIL  the EU rule table does not distinguish guidance from regulation, so an"
    echo "        interval resting on a non-binding document looks like one resting on law"
    bad=1
  fi
  return $bad
}

# ---------------------------------------------------------------- 4. the page is not stale

run_docs() {
  python3 scripts/build_page.py --check || return 1
  # And rebuild for real, then diff, in case --check and the writer ever disagree.
  python3 scripts/build_page.py > /dev/null || return 1
  if git diff --quiet -- docs/index.html; then
    echo "  ok    docs/index.html regenerated byte for byte from the committed fixtures"
  else
    echo "  FAIL  docs/index.html is not what the current code produces. The published page"
    echo "        has drifted from the engine behind it. Commit the rebuilt file."
    git --no-pager diff --stat -- docs/index.html
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------- 5. a real browser

run_browser() { timeout 300 node scripts/browser_check.mjs; }

# ---------------------------------------------------------------- 6. independent recompute

run_independent() { python3 scripts/check_independent.py; }

# ---------------------------------------------------------------- 7. privacy

run_privacy() { python3 scripts/privacy_scan.py; }

# ---------------------------------------------------------------- 8. sabotage

run_sabotage() { timeout 2400 python3 scripts/sabotage.py; }

# ---------------------------------------------------------------- 9. the README

run_readme() {
  local bad=0
  if [ ! -f README.md ]; then
    echo "  FAIL  there is no README.md"
    return 1
  fi

  # The Status section holds the pasted output of this very script, so a literal marker
  # written into this check would be found in the README by the check itself. The markers
  # are therefore assembled from fragments and matched case sensitively. The other half of
  # the problem is that the paste sits inside a fenced code block, and a scaffold marker
  # that appears only in pasted output is not a scaffold marker. So the fenced blocks are
  # stripped before the search rather than the markers being excluded, because an exclusion
  # disarms the check exactly where it is most likely to be needed.
  # HELD IN A VARIABLE, NOT WRITTEN TO THE SCRATCH DIRECTORY. This used to redirect into
  # $TMP/readme-prose.txt and grep the file three times. On 2026-09-04, during a batch that
  # re-verified a hundred repositories at once, the scratch directory was not there when the
  # redirect ran, and a repository where nothing was wrong reported a failure with five lines
  # of "No such file or directory". Nothing in this script removes $TMP after creating it and
  # the disk was not full; the cause was not found. Keeping the prose in a shell variable means
  # there is nothing on disk to go missing, which settles it without needing the explanation.
  local prose
  prose="$(python3 - README.md <<'PY'
import sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
out, fenced = [], False
for line in lines:
    if line.lstrip().startswith("```"):
        fenced = not fenced
        continue
    if not fenced:
        out.append(line)
print("\n".join(out))
PY
)"
  local markers=("TO""DO" "FIX""ME" "XX""X" "replace with a real description" "NOT YET VERIFIED")
  local found=0
  for m in "${markers[@]}"; do
    if grep -qF -- "$m" <<<"$prose"; then
      echo "  FAIL  README.md still contains a stub marker outside a code block, at:"
      grep -nF -- "$m" <<<"$prose" | head -3
      found=1
    fi
  done
  if [ "$found" -eq 0 ]; then
    echo "  ok    README.md has no stub marker in its prose, and fenced blocks were"
    echo "        stripped before looking rather than excluded from the patterns"
  else
    bad=1
  fi

  if grep -q '^## Status' README.md; then
    echo "  ok    README.md has a Status section"
  else
    echo "  FAIL  README.md has no ## Status section, so nothing records that this ever ran"
    bad=1
  fi

  if grep -qF "$SUCCESS_LINE" README.md; then
    echo "  ok    the Status section carries this script's own success line"
  else
    echo "  FAIL  README.md does not contain \"$SUCCESS_LINE\"."
    echo "        Paste the real output of this script into the Status section."
    bad=1
  fi

  # A pasted count is a claim like any other and goes stale the moment somebody adds a test.
  if [ -f "$TMP/test-count.txt" ]; then
    local n
    n="$(cat "$TMP/test-count.txt")"
    if grep -qF "$n unit tests" README.md; then
      echo "  ok    the README's test count still says $n, which is what just ran"
    else
      echo "  FAIL  the README does not say \"$n unit tests\". The suite has $n tests now."
      bad=1
    fi
  else
    echo "  FAIL  the unit test step produced no count, so the README claim went unchecked"
    bad=1
  fi

  # The two limits the README must state, because a tool that reads a panel and prints
  # confident numbers invites both misreadings.
  if grep -qi "cannot detect a mislabelled product" README.md; then
    echo "  ok    the README says the tool cannot detect a mislabelled product"
  else
    echo "  FAIL  the README does not say the tool cannot detect a mislabelled product."
    echo "        Every check here compares a label against itself, and a panel that is"
    echo "        wrong in a self-consistent way passes all of them."
    bad=1
  fi
  if grep -qi "not medical" README.md && grep -qi "dietary advice" README.md; then
    echo "  ok    the README says this is not medical or dietary advice"
  else
    echo "  FAIL  the README does not disclaim medical or dietary advice"
    bad=1
  fi
  if grep -qi "rounding" README.md; then
    echo "  ok    the README explains that every figure inherits the label's own rounding"
  else
    echo "  FAIL  the README does not mention rounding, which is the whole subject"
    bad=1
  fi
  return $bad
}

# ---------------------------------------------------------------- run them

TOTAL_STEPS=9
SUCCESS_LINE="VERIFY PASSED: $TOTAL_STEPS of $TOTAL_STEPS steps"

# A verify run must not modify the repository it is verifying. Step 4 rebuilds the page,
# the privacy scan writes into a tracked control file and restores it, and the sabotage
# harness copies the tree, so a stray write into the working copy is plausible. A run that
# edits the tree can pass on a later invocation for reasons an earlier one created, which is
# indistinguishable from working. One script in this fleet alternated between exit 0 and
# exit 1 across identical invocations before this check existed.
TRACKED_BEFORE="$(git ls-files -z 2>/dev/null | xargs -0 -r md5sum 2>/dev/null | md5sum)"

printf 'nutrition-label-normalizer verify\n\n'

step "1/9 toolchain and repository preconditions" check_toolchain
step "2/9 unit tests" run_unit_tests
step "3/9 the command line tool end to end, both halves of the corpus" run_cli
step "4/9 docs/index.html rebuilt from the fixtures and diffed" run_docs
step "5/9 the published page loaded in real headless Chrome at 1280px and 390px" run_browser
step "6/9 independent recomputation by a second implementation" run_independent
step "7/9 privacy scan with a positive control" run_privacy
step "8/9 sabotage under the three gate rule, with a null control" run_sabotage
# Last, because it looks for this script's own success line and the count the tests reported.
step "9/9 the README is finished and its numbers still match" run_readme

hr
TRACKED_AFTER="$(git ls-files -z 2>/dev/null | xargs -0 -r md5sum 2>/dev/null | md5sum)"
if [ "$TRACKED_BEFORE" = "$TRACKED_AFTER" ]; then
  printf '  ok    the run changed no tracked file, so its result is a fact about the code\n'
else
  printf '  FAIL  this run MODIFIED a tracked file. A verify that edits the tree it checks\n'
  printf '        can pass on a later run for reasons an earlier run created.\n'
  git status --short | head -5
  FAIL=$((FAIL + 1))
  FAILED_STEPS+=("the run modified a tracked file")
fi

hr
if [ "$FAIL" -eq 0 ] && [ "$PASS" -eq "$TOTAL_STEPS" ]; then
  printf '%s\n' "$SUCCESS_LINE"
  exit 0
fi
printf 'VERIFY FAILED: %d of %d steps passed, %d failed\n' "$PASS" "$TOTAL_STEPS" "$FAIL"
for s in "${FAILED_STEPS[@]:-}"; do
  [ -n "$s" ] && printf '  failed: %s\n' "$s"
done
exit 1
