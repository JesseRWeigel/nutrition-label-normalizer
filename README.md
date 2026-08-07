# nutrition-label-normalizer

Restate US and EU nutrition labels on a common basis, carrying the label's own rounding as an
interval.

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
tests/                 70 unit tests
scripts/verify.sh      the nine steps whose combined exit code is the result
scripts/check_independent.py   recomputes everything by a second implementation
scripts/sabotage.py    29 deliberate breakages under the three-gate rule
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

`scripts/sabotage.py` breaks the engine 29 ways and requires something to notice each time, under
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
PASTE_VERIFY_OUTPUT_HERE
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
