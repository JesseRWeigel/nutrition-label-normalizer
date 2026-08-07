#!/usr/bin/env python3
"""Generate docs/index.html from the fixture corpus.

The page contains no nutrition arithmetic of its own. Every interval on it is computed
here, by the same engine the CLI uses, and embedded as data. The browser code only
picks a row out of a table and draws it, which keeps a second implementation of the
rounding rules from existing and quietly disagreeing with the first.

``--check`` rebuilds into memory and compares against the committed file, so verify.sh
can fail when the page has drifted from the fixtures rather than letting a stale page
sit in public.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nutrilabel.checks import run_all  # noqa: E402
from nutrilabel.interval import format_fraction  # noqa: E402
from nutrilabel.label import Label  # noqa: E402
from nutrilabel.normalize import BASIS_CHOICES, normalize  # noqa: E402
from nutrilabel.rulesets import US_DAILY_VALUES  # noqa: E402

OUTPUT = ROOT / "docs" / "index.html"
LABEL_DIR = ROOT / "fixtures" / "labels"

# Panel order, so the table reads like a label rather than like a dictionary.
NUTRIENT_ORDER = [
    "energy_kj", "energy_kcal", "fat", "saturated_fat", "trans_fat",
    "monounsaturated_fat", "polyunsaturated_fat", "cholesterol", "sodium", "salt",
    "carbohydrate", "fiber", "total_sugars", "added_sugars", "polyols", "starch",
    "protein", "vitamin_d", "calcium", "iron", "potassium", "vitamin_a", "vitamin_c",
]

NUTRIENT_LABELS = {
    "energy_kj": "Energy (kJ)",
    "energy_kcal": "Energy (kcal)",
    "fat": "Total fat",
    "saturated_fat": "Saturated fat",
    "trans_fat": "Trans fat",
    "monounsaturated_fat": "Monounsaturated fat",
    "polyunsaturated_fat": "Polyunsaturated fat",
    "cholesterol": "Cholesterol",
    "sodium": "Sodium",
    "salt": "Salt",
    "carbohydrate": "Carbohydrate",
    "fiber": "Fibre",
    "total_sugars": "Total sugars",
    "added_sugars": "Added sugars",
    "polyols": "Polyols",
    "starch": "Starch",
    "protein": "Protein",
    "vitamin_d": "Vitamin D",
    "calcium": "Calcium",
    "iron": "Iron",
    "potassium": "Potassium",
    "vitamin_a": "Vitamin A",
    "vitamin_c": "Vitamin C",
}


def build_data() -> dict:
    labels = []
    for path in sorted(LABEL_DIR.glob("*.json")):
        label = Label.load(path)
        entry = {
            "id": path.stem,
            "name": label.name,
            "format": label.label_format,
            "provenance": label.provenance,
            "source_note": label.source_note,
            "declared_basis": label.basis,
            "medium": label.medium_unit,
            "bases": {},
            "findings": [finding.to_json() for finding in run_all(label)],
        }
        for basis in BASIS_CHOICES:
            try:
                normalized = normalize(label, basis)
            except Exception as error:  # a label with no serving cannot reach per_100g
                entry["bases"][basis] = {"unavailable": str(error)}
                continue
            entry["bases"][basis] = {
                "basis_label": normalized.basis_label,
                "serving_note": normalized.serving_note,
                "warnings": list(normalized.warnings),
                "values": {
                    key: {
                        "lo": float(value.interval.lo),
                        "hi": float(value.interval.hi),
                        "display": format_fraction(value.interval.lo, 2)
                                   + ("" if value.interval.is_point
                                      else " to " + format_fraction(value.interval.hi, 2)),
                        "unit": value.unit,
                        "rule_basis": value.rule_basis,
                        "citation": value.citation,
                        "derivation": value.derivation,
                    }
                    for key, value in normalized.values.items()
                },
            }
        labels.append(entry)

    return {
        "labels": labels,
        "nutrient_order": NUTRIENT_ORDER,
        "nutrient_labels": NUTRIENT_LABELS,
        "bases": list(BASIS_CHOICES),
        "daily_values": {key: [float(amount), unit]
                         for key, (amount, unit) in US_DAILY_VALUES.items()},
        "default_pair": ["cereal-a-flakes", "cereal-b-granola"],
    }


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nutrition label normalizer</title>
<meta name="description" content="Restate US and EU nutrition labels on a common basis, carrying the label's own rounding as an interval.">
<style>
:root {
  color-scheme: light dark;
  --bg: #fbfaf7;
  --panel: #ffffff;
  --ink: #14181d;
  --muted: #5d6773;
  --line: #dcd8d0;
  --accent: #1d5c4f;
  --accent-soft: #d7ece5;
  --warn: #8a3a12;
  --warn-soft: #fbe6d8;
  --bar: #7fb3a4;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a;
    --panel: #1c2024;
    --ink: #e9e6e0;
    --muted: #9aa3ad;
    --line: #333a41;
    --accent: #7fd0bb;
    --accent-soft: #1e3a34;
    --warn: #f0a878;
    --warn-soft: #3a2416;
    --bar: #3f6f63;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 68rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
h1 { font-size: 1.6rem; line-height: 1.2; margin: 0 0 .4rem; letter-spacing: -.01em; }
h2 { font-size: 1.05rem; margin: 2rem 0 .6rem; }
p { margin: .5rem 0; }
.lede { color: var(--muted); max-width: 46rem; }
.disclaimer {
  border-left: 3px solid var(--warn);
  background: var(--warn-soft);
  color: var(--ink);
  padding: .6rem .8rem;
  margin: 1rem 0;
  font-size: .9rem;
  border-radius: 0 4px 4px 0;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 1rem;
  margin: 1rem 0;
}
.field { display: flex; flex-direction: column; gap: .3rem; min-width: 0; flex: 1 1 14rem; }
.field label { font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
select {
  font: inherit;
  padding: .4rem .5rem;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--bg);
  color: var(--ink);
  /* A select sizes itself to its widest option and will push the page sideways on a
     narrow viewport unless it is allowed to shrink. min-width:0 on the flex item and
     width:100% here is the fix; hiding the overflow on body would only hide it. */
  width: 100%;
  max-width: 100%;
}
.basis-row { display: flex; flex-wrap: wrap; gap: .4rem; }
.basis-row button {
  font: inherit;
  font-size: .88rem;
  padding: .35rem .7rem;
  border: 1px solid var(--line);
  background: var(--bg);
  color: var(--ink);
  border-radius: 999px;
  cursor: pointer;
}
.basis-row button[aria-pressed="true"] {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--bg);
  font-weight: 600;
}
#basis-banner {
  font-size: 1rem;
  font-weight: 600;
  background: var(--accent-soft);
  color: var(--ink);
  border: 1px solid var(--accent);
  border-radius: 6px;
  padding: .55rem .8rem;
  margin: 1rem 0 .4rem;
}
#basis-detail { color: var(--muted); font-size: .87rem; margin: 0 0 1rem; }
.tablewrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }
table { border-collapse: collapse; width: 100%; min-width: 34rem; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: .5rem .7rem; border-bottom: 1px solid var(--line); font-size: .92rem; }
th { font-size: .78rem; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600; }
td.num, th.num { text-align: right; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
.unit { color: var(--muted); font-size: .82rem; }
.inferred { color: var(--warn); font-weight: 600; cursor: help; }
.bar { height: 6px; background: var(--bar); border-radius: 3px; min-width: 2px; }
.barcell { width: 30%; min-width: 5rem; }
.findings { display: grid; gap: .8rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr)); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: .8rem 1rem; min-width: 0; }
.card h3 { margin: 0 0 .3rem; font-size: .98rem; }
.card .prov { color: var(--muted); font-size: .8rem; margin-bottom: .5rem; }
.finding { font-size: .85rem; margin: .35rem 0; padding-left: .8rem; border-left: 3px solid var(--line); overflow-wrap: anywhere; }
.finding.flag { border-left-color: var(--warn); }
.finding .code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .78rem; color: var(--muted); }
footer { margin-top: 3rem; color: var(--muted); font-size: .85rem; }
footer a { color: var(--accent); }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .86em; }
</style>
</head>
<body>
<div class="wrap">
<h1>Nutrition label normalizer</h1>
<p class="lede">Two products are only comparable once their labels are on the same
basis. This page restates each label per 100 g, per serving, per 100 kcal and per
container, and carries the label's own rounding as an interval instead of pretending
the printed figure is exact.</p>

<div class="disclaimer">Not medical or dietary advice. This tool cannot tell you
whether a package is labelled correctly. It reads only what the panel says, so every
figure here inherits the panel's own rounding, and a product that is mislabelled in an
internally consistent way passes every check on this page.</div>

<div class="controls">
  <div class="field">
    <label for="pick-a">Product A</label>
    <select id="pick-a"></select>
  </div>
  <div class="field">
    <label for="pick-b">Product B</label>
    <select id="pick-b"></select>
  </div>
  <div class="field">
    <label id="basis-label" for="basis-per_100g">Basis</label>
    <div class="basis-row" id="basis-row" role="group" aria-labelledby="basis-label"></div>
  </div>
</div>

<div id="basis-banner" aria-live="polite">Basis in force: loading</div>
<p id="basis-detail"></p>

<div class="tablewrap">
  <table id="comparison">
    <thead>
      <tr>
        <th scope="col">Nutrient</th>
        <th scope="col" class="num" id="head-a">A</th>
        <th scope="col" class="barcell">Spread</th>
        <th scope="col" class="num" id="head-b">B</th>
        <th scope="col">Unit</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
</div>

<h2>What the labels say about themselves</h2>
<div class="findings" id="findings"></div>

<footer>
<p>Rounding rules from 21 CFR 101.9 as amended by the 2016 final rule (81 FR 33742) and
from Regulation (EU) No 1169/2011 with the European Commission's December 2012 tolerance
guidance. Figures marked <span class="inferred">inferred</span> rest on the precision
printed on the panel rather than on a cited rounding step.</p>
<p>Generated from the fixture corpus by <code>scripts/build_page.py</code>. Do not edit
this file by hand; <code>scripts/verify.sh</code> rebuilds it and fails on any
difference.</p>
</footer>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("data").textContent);
  var BASIS_NAMES = {
    per_serving: "Per serving",
    per_100g: "Per 100 g or ml",
    per_100kcal: "Per 100 kcal",
    per_container: "Per container"
  };

  var state = {
    a: DATA.default_pair[0],
    b: DATA.default_pair[1],
    basis: "per_100g"
  };

  function byId(id) {
    for (var i = 0; i < DATA.labels.length; i++) {
      if (DATA.labels[i].id === id) { return DATA.labels[i]; }
    }
    return null;
  }

  function fillSelect(node, selected) {
    node.textContent = "";
    DATA.labels.forEach(function (label) {
      var option = document.createElement("option");
      option.value = label.id;
      option.textContent = label.name;
      if (label.id === selected) { option.selected = true; }
      node.appendChild(option);
    });
  }

  function buildBasisButtons() {
    var row = document.getElementById("basis-row");
    row.textContent = "";
    DATA.bases.forEach(function (basis) {
      var button = document.createElement("button");
      button.type = "button";
      button.id = "basis-" + basis;
      button.dataset.basis = basis;
      button.textContent = BASIS_NAMES[basis] || basis;
      button.setAttribute("aria-pressed", basis === state.basis ? "true" : "false");
      button.addEventListener("click", function () {
        state.basis = basis;
        render();
      });
      row.appendChild(button);
    });
  }

  function basisBlock(label) {
    var block = label.bases[state.basis];
    return block && !block.unavailable ? block : null;
  }

  function cell(value) {
    if (!value) { return { text: "\\u2014", lo: null, hi: null }; }
    return { text: value.display, lo: value.lo, hi: value.hi, unit: value.unit,
             ruleBasis: value.rule_basis, derivation: value.derivation };
  }

  function render() {
    var a = byId(state.a);
    var b = byId(state.b);
    var blockA = basisBlock(a);
    var blockB = basisBlock(b);

    document.querySelectorAll("#basis-row button").forEach(function (button) {
      button.setAttribute("aria-pressed",
        button.dataset.basis === state.basis ? "true" : "false");
    });

    var labels = [];
    if (blockA) { labels.push(blockA.basis_label); }
    if (blockB && (!blockA || blockB.basis_label !== blockA.basis_label)) {
      labels.push(blockB.basis_label);
    }
    document.getElementById("basis-banner").textContent =
      "Basis in force: " + (labels.length ? labels.join("  and  ") : "not available");

    var detail = [];
    [[a, blockA], [b, blockB]].forEach(function (pair) {
      var label = pair[0], block = pair[1];
      if (!block) {
        detail.push(label.name + ": " + (label.bases[state.basis].unavailable || "unavailable"));
        return;
      }
      detail.push(label.name + ": " + block.serving_note);
      block.warnings.forEach(function (warning) {
        detail.push(label.name + ": " + warning);
      });
    });
    document.getElementById("basis-detail").textContent = detail.join("  \\u00b7  ");

    document.getElementById("head-a").textContent = a.name;
    document.getElementById("head-b").textContent = b.name;

    var body = document.getElementById("rows");
    body.textContent = "";
    DATA.nutrient_order.forEach(function (key) {
      var valueA = blockA ? blockA.values[key] : null;
      var valueB = blockB ? blockB.values[key] : null;
      if (!valueA && !valueB) { return; }
      var cellA = cell(valueA);
      var cellB = cell(valueB);

      var tr = document.createElement("tr");
      tr.dataset.nutrient = key;

      var th = document.createElement("th");
      th.scope = "row";
      th.textContent = DATA.nutrient_labels[key] || key;
      tr.appendChild(th);

      [cellA, cellB].forEach(function (item, index) {
        var td = document.createElement("td");
        td.className = "num";
        td.dataset.side = index === 0 ? "a" : "b";
        td.textContent = item.text;
        if (item.ruleBasis && item.ruleBasis !== "cited") {
          var mark = document.createElement("span");
          mark.className = "inferred";
          mark.textContent = " \\u25b2";
          mark.title = "Interval inferred from the printed precision, not from a cited "
            + "rounding step.";
          td.appendChild(mark);
        }
        if (item.derivation) { td.title = item.derivation; }
        if (index === 0) { tr.appendChild(td); tr.appendChild(spreadCell(cellA, cellB)); }
        else { tr.appendChild(td); }
      });

      var unit = document.createElement("td");
      unit.className = "unit";
      unit.textContent = (valueA || valueB).unit;
      tr.appendChild(unit);

      body.appendChild(tr);
    });

    renderFindings([a, b]);
  }

  function spreadCell(cellA, cellB) {
    var td = document.createElement("td");
    td.className = "barcell";
    var top = 0;
    [cellA, cellB].forEach(function (item) {
      if (item.hi !== null && item.hi > top) { top = item.hi; }
    });
    if (top <= 0) { td.textContent = ""; return td; }
    [cellA, cellB].forEach(function (item) {
      if (item.lo === null) { return; }
      var bar = document.createElement("div");
      bar.className = "bar";
      var width = Math.max(2, Math.round(((item.hi - item.lo) / top) * 100));
      var offset = Math.round((item.lo / top) * 100);
      bar.style.width = width + "%";
      bar.style.marginLeft = Math.min(offset, 100 - width) + "%";
      bar.style.marginBottom = "3px";
      td.appendChild(bar);
    });
    return td;
  }

  function renderFindings(labels) {
    var host = document.getElementById("findings");
    host.textContent = "";
    labels.forEach(function (label) {
      var card = document.createElement("div");
      card.className = "card";
      card.dataset.label = label.id;

      var heading = document.createElement("h3");
      heading.textContent = label.name;
      card.appendChild(heading);

      var provenance = document.createElement("p");
      provenance.className = "prov";
      provenance.textContent = label.format.replace("_", " ") + " \\u00b7 "
        + label.provenance + " \\u00b7 " + label.source_note;
      card.appendChild(provenance);

      if (!label.findings.length) {
        var none = document.createElement("p");
        none.className = "finding";
        none.textContent = "No findings.";
        card.appendChild(none);
      }
      label.findings.forEach(function (finding) {
        var row = document.createElement("p");
        row.className = "finding " + finding.severity;
        var code = document.createElement("span");
        code.className = "code";
        code.textContent = finding.severity === "flag" ? "FLAG " : "note ";
        row.appendChild(code);
        row.appendChild(document.createTextNode(finding.message));
        card.appendChild(row);
      });
      host.appendChild(card);
    });
  }

  var pickA = document.getElementById("pick-a");
  var pickB = document.getElementById("pick-b");
  fillSelect(pickA, state.a);
  fillSelect(pickB, state.b);
  pickA.addEventListener("change", function () { state.a = pickA.value; render(); });
  pickB.addEventListener("change", function () { state.b = pickB.value; render(); });
  buildBasisButtons();
  render();
  window.__nutrilabelReady = true;
})();
</script>
</body>
</html>
"""


def render_page() -> str:
    data = json.dumps(build_data(), separators=(",", ":"), sort_keys=True)
    # The payload sits inside a <script type="application/json"> block, so the only
    # sequence that could end it early is a literal closing script tag.
    data = data.replace("</", "<\\/")
    return PAGE.replace("__DATA__", data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail if the committed page differs from a fresh build")
    args = parser.parse_args()

    rendered = render_page()
    if args.check:
        if not OUTPUT.exists():
            print(f"build_page: {OUTPUT} does not exist. Run scripts/build_page.py.",
                  file=sys.stderr)
            return 1
        current = OUTPUT.read_text(encoding="utf-8")
        if current != rendered:
            print("build_page: docs/index.html is stale. The fixtures or the engine have "
                  "changed since it was generated. Run: python3 scripts/build_page.py",
                  file=sys.stderr)
            return 1
        print(f"page: docs/index.html matches a fresh build ({len(rendered)} bytes)")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"page: wrote {OUTPUT.relative_to(ROOT)} ({len(rendered)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
