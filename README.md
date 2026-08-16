# nutrition-label-normalizer

Restate US and EU nutrition labels on a common basis, carrying the label's own rounding as an
interval.

**[Two labels restated on the same basis →](https://jesserweigel.github.io/nutrition-label-normalizer/)**

Catalog task: `HLTH-015`. One of a public catalog of build ideas:
https://github.com/JesseRWeigel/722-things-to-build

## What this is

Two boxes of cereal sit on the same shelf. One declares a 34 g serving, the other 55 g. Per
serving the second looks 60 percent larger on almost every row. Per 100 g they are nearly the
same food. The serving size is what makes the comparison impossible, and it is printed in the
smallest type on the panel.

This tool reads a panel, US or EU, and restates every row on a basis you choose: per serving,
per 100 g or 100 ml, per 100 kcal, or per container. It names the basis in force above every
column, because a number with no basis stated is how the comparison goes wrong to begin with.

The second thing it does matters more. A nutrition label does not round to significant figures,
it rounds by rule. Under 21 CFR 101.9 fat below 0.5 g is printed as `0`, calories go to the
nearest 5 below 50 and the nearest 10 above, and sodium switches from 5 mg steps to 10 mg steps
at 140 mg. So `0 g trans fat` is a statement that the true value lies somewhere in [0, 0.5), and
there is no point value in there to recover. This tool never pretends otherwise. Every figure it
prints is an interval, and multiplying a rounded per-serving figure by a rounded serving count
compounds both roundings, which is why a box of thirteen servings declaring zero trans fat comes
out at 0 to 6.75 g of it.

```
$ nutrilabel normalize fixtures/labels/cereal-a-flakes.json --basis per_container
Cereal A, flakes [US_2016, declared per_serving, serving 34 g (1 cup)]
BASIS IN FORCE: per container  (per_container)
  12.5–13.5 servings per container
  note: servings per container is itself rounded to 12.5–13.5, so every per-container figure
  below carries that width on top of the per-serving rounding.

  nutrient              low        high  unit   rule
  energy_kcal        1687.5      1957.5  kcal   21 CFR 101.9(c)(1)
  fat                21.875      30.375  g      21 CFR 101.9(c)(2)
  trans_fat               0        6.75  g      21 CFR 101.9(c)(2)
  sodium            1718.75      1957.5  mg     21 CFR 101.9(c)(4)
  potassium         1493.75     1626.75  mg     no rounding rule cited; step inferred  <- inferred
```

The third thing is a cross-check the label makes possible against itself. Protein, carbohydrate
and fat carry known energy per gram, so a declared calorie figure is a prediction the panel makes
about its own other rows. There is no tolerance constant anywhere in that check. Both sides are
intervals derived from the rounding rules, and a flag is raised only when no assignment of true
values consistent with the printed panel could produce the printed energy. That is what keeps the
negative controls meaningful: twelve of the nineteen fixtures are correct labels that must raise
nothing, and a checker that flags everything would fail on them.

## Running it

Python 3.10 or newer. No dependencies; the engine is standard library only.

```bash
# every figure per 100 g, so two products of different serving size become comparable
python3 -m nutrilabel normalize fixtures/labels/fda-sample-panel.json --basis per_100g

# two labels side by side on one basis, with the ratio between them as an interval
python3 -m nutrilabel compare fixtures/labels/cereal-a-flakes.json \
                              fixtures/labels/cereal-b-granola.json --basis per_100g

# cross-check each label against itself; exit 1 if any of them contradicts itself
python3 -m nutrilabel check fixtures/labels/*.json

# read a panel from Tesseract TSV, refusing low-confidence tokens rather than guessing
tesseract panel.png stdout -c tessedit_create_tsv=1 tsv > panel.tsv
python3 -m nutrilabel ocr panel.tsv

# the rounding rule tables, with the citation and the basis behind each one
python3 -m nutrilabel rules --format EU_1169
```

`--json` on any of the first four gives machine-readable output, with exact rationals alongside
the floats so nothing is lost to binary floating point.

Verify command, whose exit code is the whole result:

```bash
bash scripts/verify.sh
```

## What it will not tell you

**This is not medical or dietary advice.** It is an arithmetic tool for reading a printed panel.

**It cannot detect a mislabelled product.** Every check here compares a label against itself,
because the panel is the only evidence available. A product whose panel is wrong in a
self-consistent way passes every check in this repository. Detecting that needs a laboratory.

**Every figure inherits the label's own rounding, and cannot be narrower than it.** The intervals
are not error bars on a measurement. They are the exact set of true values that the printed
figure is consistent with under the declaration rules, and moving a per-serving figure to a
per-100 g basis divides by a serving size that is itself rounded, so the result is wider than
what you started with. That widening is real information about how much of the difference between
two products you are entitled to believe.

**The EU rounding steps are guidance, not statute.** They come from the European Commission's
December 2012 guidance on tolerances, which is non-binding, and real labels routinely print finer
figures than it calls for. Where a panel is finer than its rule, the interval is taken from the
printed precision instead and the output says so. Rules are marked `cited`, `guidance` or
`inferred`, and an interval resting on a guess is never displayed as if it rested on a regulation.

**The daily values are the current ones.** The 2016 final rule (81 FR 33742) added added sugars
and potassium to the mandatory panel, made vitamin A and vitamin C voluntary, moved sodium from
2400 mg to 2300 mg and fibre from 25 g to 28 g. A panel printed before that change is detected by
its shape and noted, because comparing it against a current one on those rows produces a
difference that is an artefact of the format change rather than of the food.

**kJ and kcal are not related by one constant on an EU label.** Annex XIV of Regulation (EU) No
1169/2011 gives two columns whose ratios run from 4.0 for fibre to 4.25 for carbohydrate, so an
olive oil legitimately sits near 4.11 and dividing its kJ figure by the thermochemical 4.184 does
not give its kcal figure. The two columns are checked separately and the ratio is reported as a
note rather than flagged. The 4.184 constant is used only where an actual physical quantity of
energy is being converted.

**Salt is sodium times 2.5**, from Annex I point 11. A US panel declares sodium and never salt, an
EU panel declares salt and never sodium, and comparing them without the factor overstates the EU
product by 2.5x.

## Layout

```
nutrilabel/            the engine, standard library only
  interval.py          exact intervals over the rationals
  rounding.py          declaration rules, forward and inverse, checked against each other
  rulesets.py          the US and EU tables, all data, each entry cited
  label.py             what was printed and on what basis
  normalize.py         restating a label on a chosen basis
  energy.py            the Atwater cross-check, tolerance taken from the rounding rules
  checks.py            internal consistency checks on one label
  ocr.py               Tesseract TSV in, refusals rather than guesses out
  units.py             mass, volume and energy conversion
fixtures/labels/       19 labels: 12 correct, 7 broken on purpose, each with a provenance
fixtures/expected/     hand-computed intervals and the flag each broken label must raise
tests/                 73 unit tests
scripts/verify.sh      the nine steps whose combined exit code is the result
scripts/check_independent.py   recomputes everything by a second implementation
scripts/sabotage.py    30 deliberate breakages under the three-gate rule
scripts/privacy_scan.py        credential sweep with a positive control
scripts/build_page.py  generates docs/index.html from the fixtures
docs/index.html        the published page, rebuilt and diffed by verify
```

## How this is checked

The fixture corpus is the foundation, and its most important half is the twelve labels that must
raise nothing. A checker built only from broken labels is passed by a function that returns
"broken" for every input.

`scripts/check_independent.py` recomputes the intervals, the basis conversions and the energy
cross-check without importing anything from the package, and proves that independence by walking
its own syntax tree with `ast` rather than by grepping for an import statement. The routes are
deliberately different: the rounding inverses come from bisection on a forward rule written out
again from the regulation, so there is no segment table there to be wrong in the same way, and
the basis conversions come from corner enumeration, which does not know that interval division
swaps the endpoints and so disagrees if the engine ever forgets.

`scripts/sabotage.py` breaks the engine 30 ways and requires something to notice each time, under
three gates: the patch must apply, it must change what the tool computes, and only then must a
check fail. A null control runs first, copying the tree unchanged into another directory and
requiring an identical fingerprint, because a measurement that tracks where the code lives rather
than what it does hands the second gate a free pass to every attack.

Two of those sabotages found real gaps rather than confirming existing checks, and both were
fixed rather than deleted. Disabling the contiguity check inside `RoundingRule.inverse` changed
nothing anywhere, because no rule table in the repository has a hole in it; the fix is a test that
builds one that does. Skipping the unit conversion before the rounding inverse also changed
nothing, because no fixture declared a nutrient in a unit other than its rule's; the fix is
`us-export-sodium-in-grams`, a panel with sodium transcribed in grams that also serves as a
negative control.

## Status

`bash scripts/verify.sh`, run from a clean checkout:

```
nutrition-label-normalizer verify

------------------------------------------------------------------------
1/9 toolchain and repository preconditions
------------------------------------------------------------------------
  ok    python 3.12
  ok    no Python dependency file, so the engine is standard library only
  ok    node v24.13.0, which drives the browser check
  ok    a Chrome binary is present at /usr/bin/google-chrome
  ok    inside a git repository, so the privacy scan has a file list to work from

[PASS] 1/9 toolchain and repository preconditions

------------------------------------------------------------------------
2/9 unit tests
------------------------------------------------------------------------
  ok    73 tests passed

[PASS] 2/9 unit tests

------------------------------------------------------------------------
3/9 the command line tool end to end, both halves of the corpus
------------------------------------------------------------------------
  ok    check exits 0 on all 12 internally consistent labels
  ok    check exits 1 on all 7 labels that contradict themselves
  ok    7 flags raised across 7 broken labels
  ok    --basis per_serving states the basis in force on its first line
  ok    --basis per_100g states the basis in force on its first line
  ok    --basis per_100kcal states the basis in force on its first line
  ok    --basis per_container states the basis in force on its first line
  ok    the per-container comparison warns that two roundings compound
  ok    a smudged panel produces refusals rather than guessed digits
  ok    the ambiguous glyph in '16Omg' was refused, not corrected to 160
  ok    the US rule table prints its citations
  ok    the EU rule table marks itself as resting on guidance, not statute

[PASS] 3/9 the command line tool end to end, both halves of the corpus

------------------------------------------------------------------------
4/9 docs/index.html rebuilt from the fixtures and diffed
------------------------------------------------------------------------
page: docs/index.html matches a fresh build (269428 bytes)
  ok    docs/index.html regenerated byte for byte from the committed fixtures

[PASS] 4/9 docs/index.html rebuilt from the fixtures and diffed

------------------------------------------------------------------------
5/9 the published page loaded in real headless Chrome at 1280px and 390px
------------------------------------------------------------------------
browser: /usr/bin/google-chrome
viewport desktop (1280x900)
  ok   desktop: the script ran and produced rows
  ok   desktop: the basis in force is stated
  ok   desktop: cereal A energy is an interval, not a point
  ok   desktop: exactly one basis button is pressed
  ok   desktop: both findings cards rendered
  ok   desktop: per 100 g the two cereals overlap
  ok   desktop: body does not hide horizontal overflow
  ok   desktop: nothing escapes the viewport horizontally
  ok   desktop: the document does not scroll sideways
  ok   desktop: the basis switch changes the stated basis
  ok   desktop: the basis switch changes the numbers
  ok   desktop: per serving the granola looks far larger
  ok   desktop: changing the product changes the column heading
  ok   desktop: a declared 0 g trans fat reaches past a gram per container
  ok   desktop: the compounding warning is shown
  ok   desktop: a self-contradicting label shows a flag
viewport narrow (390x844)
  ok   narrow: the script ran and produced rows
  ok   narrow: the basis in force is stated
  ok   narrow: cereal A energy is an interval, not a point
  ok   narrow: exactly one basis button is pressed
  ok   narrow: both findings cards rendered
  ok   narrow: per 100 g the two cereals overlap
  ok   narrow: body does not hide horizontal overflow
  ok   narrow: nothing escapes the viewport horizontally
  ok   narrow: the document does not scroll sideways
  ok   narrow: the basis switch changes the stated basis
  ok   narrow: the basis switch changes the numbers
  ok   narrow: per serving the granola looks far larger
  ok   narrow: changing the product changes the column heading
  ok   narrow: a declared 0 g trans fat reaches past a gram per container
  ok   narrow: the compounding warning is shown
  ok   narrow: a self-contradicting label shows a flag

browser: all page checks passed at 1280px and 390px

[PASS] 5/9 the published page loaded in real headless Chrome at 1280px and 390px

------------------------------------------------------------------------
6/9 independent recomputation by a second implementation
------------------------------------------------------------------------
independence
  ok    an ast walk of this file finds no import of the package, static or dynamic
rounding inverses recomputed by bisection on a separately written forward rule
  ok    1561 interval endpoints recomputed across 19 labels
the hand-computed expectations, against a third derivation
  ok    28 endpoints against fixtures/expected/normalisations.json
Atwater cross-check verdicts
  ok    138 energy verdicts recomputed from the macronutrients
flag sets, including the negative controls
  ok    19 flag sets, 12 of them labels that must stay clean
the headline claims
  per serving, the granola reads 1.55x to 1.74x the flakes
  per 100 g, the same two read 0.94x to 1.10x, which spans 1.0
  a panel reading 0 g trans fat per serving permits up to 6.75 g in the container

independent recomputation agreed on all 1755 comparisons

[PASS] 6/9 independent recomputation by a second implementation

------------------------------------------------------------------------
7/9 privacy scan with a positive control
------------------------------------------------------------------------
  ok    55 tracked files opened and read in full, nothing credential shaped
  ok    scripts/privacy_scan.py was scanned like every other file, with no self exclusion
  ok    no tracked file contains a NUL byte, so a grep based scan would see them all
  ok    no committed blob contains a NUL byte either, not just the working copy
  ok    the published page has no machine path or credential in it
  ok    positive control: 4 planted strings all found in fixtures/privacy_control.txt, so the sweep really reads tracked files
privacy scan clean, and proved able to find what it looks for

[PASS] 7/9 privacy scan with a positive control

------------------------------------------------------------------------
8/9 sabotage under the three gate rule, with a null control
------------------------------------------------------------------------
  baseline fingerprint 67f1f5bcf0435a34bbd04154
  ok    null control: an untouched copy in a different directory digests identically
  ok    inverse-half-width
          every interval is half as wide as the rounding rule permits, so the tool understates the uncertainty on every figure it prints while still looking like it is carrying one
          changed: fingerprint 67f1f5bcf043 -> f01b2943d48f
          caught by: unit suite, independent recomputation, page freshness
  ok    ties-round-down
          a true 2.5 g declares as 2 rather than 3, which disagrees with every rounding table in the guidance documents and shifts the forward rule at exactly the boundaries the inverse is derived from
          changed: fingerprint 67f1f5bcf043 -> 566eb57a1ed2
          caught by: unit suite
  ok    printed-precision-never-refines
          an EU panel printing 91.6 g of fat is read through a whole-gram step, claiming the true value lies anywhere in [91.1, 92.1) when the printed figure rules that out
          changed: fingerprint 67f1f5bcf043 -> 2a443c615863
          caught by: unit suite, independent recomputation, page freshness
  ok    unprintable-declaration-accepted
          a figure no true value could print, 1 kcal under a 5 kcal step, silently returns an interval instead of raising, so a mistyped panel is normalised rather than refused. Filed as an attack rather than a guard because it does move the output: the fingerprint sweeps every rule with a dense grid of declarations, so the guard is exercised there and is not dormant
          changed: fingerprint 67f1f5bcf043 -> c26620d028d8
          caught by: unit suite
  ok    non-contiguous-inverse-accepted [guard]
          a gap in the rule table stops being an error, so an inverse that omits a range of true values is returned as if it were complete and every figure derived from it is quietly too narrow
          changed: output unchanged, as a dormant guard must be
          caught by: unit suite
  ok    division-forgets-the-endpoint-swap
          dividing by the smallest serving gives the smallest per-gram figure, which is backwards; every per-100 g interval comes out too narrow and on the wrong side
          changed: fingerprint 67f1f5bcf043 -> be87c6da09b8
          caught by: unit suite, independent recomputation, page freshness
  ok    everything-overlaps
          no two intervals are ever disjoint, so no label ever contradicts itself and the checker becomes a function that returns 'fine'
          changed: fingerprint 67f1f5bcf043 -> 1069b23862e7
          caught by: unit suite, independent recomputation, page freshness
  ok    display-uses-bankers-rounding
          every printed figure ending in a 5 rounds down, so the page and the CLI disagree with the exact values underneath them
          changed: fingerprint 67f1f5bcf043 -> 3854221f88c7
          caught by: independent recomputation, page freshness
  ok    sodium-step-boundary-moved
          sodium above 40 mg is read as rounded to the nearest 10 rather than the nearest 5, widening every sodium interval between 40 and 140 mg for no reason in the regulation
          changed: fingerprint 67f1f5bcf043 -> 1d6b66f6c40a
          caught by: unit suite, independent recomputation, page freshness
  ok    calorie-step-boundary-moved
          a declared 230 kcal is read as [227.5, 232.5) instead of [225, 235), which is the single most consequential interval on a US panel
          changed: fingerprint 67f1f5bcf043 -> 3670c799b9ed
          caught by: unit suite, independent recomputation, page freshness
  ok    trans-fat-zero-threshold-moved
          a declared 0 g trans fat means below 0.05 g rather than below 0.5 g, so the headline claim, that a box of 13 servings can hold 6.5 g of it, collapses by a factor of ten
          changed: fingerprint 67f1f5bcf043 -> c87d9a3dc0dc
          caught by: unit suite, independent recomputation, page freshness
  ok    pre-2016-sodium-daily-value
          the daily value reverts to the pre-2016 figure, so every percent daily value the tool recomputes is measured against a reference no current label uses
          changed: fingerprint 67f1f5bcf043 -> 76cca5e80cc7
          caught by: page freshness
  ok    pre-2016-fibre-daily-value
          same, for fibre, where the 2016 rule moved the reference from 25 g to 28 g
          changed: fingerprint 67f1f5bcf043 -> 926ab95d352b
          caught by: page freshness
  ok    salt-factor-wrong
          the Annex I salt factor stops being 2.5, so a US sodium figure and an EU salt figure no longer line up and the salt cross-check drifts
          changed: fingerprint 67f1f5bcf043 -> 3040e44fc93d
          caught by: unit suite, page freshness
  ok    atwater-fat-factor-wrong
          fat is billed at 4 kcal per gram, so the energy cross-check predicts far too little for anything fatty and starts calling correct labels inconsistent
          changed: fingerprint 67f1f5bcf043 -> f45fb0b9beb3
          caught by: unit suite, independent recomputation, page freshness
  ok    eu-fat-kcal-factor-wrong
          the Annex XIV kcal column for fat becomes the carbohydrate figure, which is the mistake that makes an olive oil label look wrong
          changed: fingerprint 67f1f5bcf043 -> 2cb97aeec420
          caught by: unit suite, independent recomputation, page freshness
  ok    fibre-subtraction-uses-the-wrong-end
          the lowest energy a permitted method can give is computed from the wrong end of the fibre interval, narrowing the band the tolerance is taken from
          changed: fingerprint 67f1f5bcf043 -> a23927a2e073
          caught by: independent recomputation, page freshness
  ok    energy-check-always-consistent
          the check that makes the tool worth having returns a pass on every label, including one whose calories contradict its own macronutrients by 75 kcal
          changed: fingerprint 67f1f5bcf043 -> a2c91dba6efc
          caught by: unit suite, independent recomputation, page freshness
  ok    component-sums-never-exceed
          saturated fat may exceed total fat by a factor of ten before anything is said
          changed: fingerprint 67f1f5bcf043 -> 6bd0f11c5f9d
          caught by: unit suite, independent recomputation, page freshness
  ok    eu-carbohydrate-includes-fibre
          the US definition of total carbohydrate is applied to EU panels, where fibre is declared alongside carbohydrate rather than inside it, so every high-fibre product in Europe is flagged as self-contradictory
          changed: fingerprint 67f1f5bcf043 -> 17718a8092de
          caught by: unit suite, independent recomputation, page freshness
  ok    mass-budget-allows-a-kilo-per-hundred-grams
          the macronutrients may outweigh the food by ten to one, so a transposed serving size stops being detectable
          changed: fingerprint 67f1f5bcf043 -> c33ea78f11ab
          caught by: unit suite, independent recomputation, page freshness
  ok    declared-daily-value-never-checked
          a panel printing 25 percent where its own sodium figure gives 7 percent passes without comment
          changed: fingerprint 67f1f5bcf043 -> f943cedd8372
          caught by: unit suite, independent recomputation, page freshness
  ok    added-sugars-may-exceed-total
          added sugars, which are a subset of total sugars by definition, may exceed them by 100 g before anything is said
          changed: fingerprint 67f1f5bcf043 -> 691f1dbcc11c
          caught by: unit suite, independent recomputation, page freshness
  ok    per-container-ignores-the-serving-count
          the per-container column becomes the per-serving column, which is precisely the 'servings per container: 2.5' trick the tool exists to undo
          changed: fingerprint 67f1f5bcf043 -> 5cbca7b21cb6
          caught by: unit suite, independent recomputation, page freshness
  ok    per-hundred-factor-inverted
          per 100 g figures are multiplied by the serving size instead of divided, so two products are compared on a basis that is neither of theirs
          changed: fingerprint 67f1f5bcf043 -> 96881716dc68
          caught by: unit suite, independent recomputation, page freshness
  ok    compounding-warning-suppressed
          the tool stops saying that a rounded per-serving figure multiplied by a rounded serving count carries both roundings, which is the one thing a reader most needs told
          changed: fingerprint 67f1f5bcf043 -> f72f9bdcd04a
          caught by: unit suite, page freshness
  ok    compare-drops-the-warnings
          the side-by-side view stops carrying the compounding note, so the one place two products are weighed against each other is the one place the reader is not told the numbers are wider than they look. This is the bug verify.sh found
          changed: fingerprint 67f1f5bcf043 -> 5c4aca1578af
          caught by: unit suite
  ok    ocr-confidence-gate-removed
          a token the engine was 41 percent sure of is accepted as a fact, which is the one behaviour the task brief names: flag low confidence rather than guessing a digit
          changed: fingerprint 67f1f5bcf043 -> 6913beed9a64
          caught by: unit suite
  ok    ambiguous-glyphs-corrected-not-refused
          '16Omg' is silently corrected to 160 mg rather than refused, so a guess enters the pipeline wearing the same face as a reading
          changed: fingerprint 67f1f5bcf043 -> 27ca289c5ccd
          caught by: unit suite
  ok    label-unit-conversion-skipped
          a figure printed in milligrams is inverted through a rule written in grams, so the interval is out by a factor of a thousand
          changed: fingerprint 67f1f5bcf043 -> d76f6acaf561
          caught by: unit suite, independent recomputation, page freshness
30 of 30 sabotages passed all three gates and were caught

[PASS] 8/9 sabotage under the three gate rule, with a null control

------------------------------------------------------------------------
9/9 the README is finished and its numbers still match
------------------------------------------------------------------------
  ok    README.md has no stub marker in its prose, and fenced blocks were
        stripped before looking rather than excluded from the patterns
  ok    README.md has a Status section
  ok    the Status section carries this script's own success line
  ok    the README's test count still says 73, which is what just ran
  ok    the README says the tool cannot detect a mislabelled product
  ok    the README says this is not medical or dietary advice
  ok    the README explains that every figure inherits the label's own rounding

[PASS] 9/9 the README is finished and its numbers still match

------------------------------------------------------------------------
  ok    the run changed no tracked file, so its result is a fact about the code
------------------------------------------------------------------------
VERIFY PASSED: 9 of 9 steps
```

## What is not done

- **OCR input is Tesseract TSV, not an image.** The task says "photograph a panel", and this
  stops one step short: you run Tesseract yourself and pass it the TSV. Bundling an OCR engine
  would add a large binary dependency to a tool that is otherwise standard library only, and the
  interesting part, refusing a token the engine is unsure of rather than guessing a digit, is
  entirely on this side of that line. The `fixtures/ocr/` corpus is hand-written TSV in
  Tesseract's exact column format.
- **The row matcher is English only.** An EU panel in French or German will not be recognised by
  `nutrilabel ocr`, though its values can be entered as JSON and normalised normally.
- **Canada, Australia and New Zealand are not modelled.** They use per-serving panels with their
  own rounding tables and their own reference amounts. The rule table structure would take them,
  and nobody has typed them in.
- **Two US rule citations are given at section level only.** The serving size and servings per
  container rules cite 21 CFR 101.9(b) without a subparagraph, because we are not confident of
  the numbering. The rules themselves are right; the pointer is coarse.
- **The `%DV` recomputation covers the nutrients with a reference amount in the tables.** A panel
  declaring a percentage for something not in `US_DAILY_VALUES` produces a note saying it could
  not be checked, rather than a silent pass.

## License

MIT.
