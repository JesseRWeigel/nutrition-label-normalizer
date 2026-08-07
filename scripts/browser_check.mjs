/**
 * Load docs/index.html in real headless Chrome and measure the page from inside it.
 *
 * A screenshot is not evidence. `chrome --headless --screenshot --window-size=` can
 * render at a width other than the one it captures, so every assertion here runs as
 * JavaScript in the page and reports numbers the script must have produced.
 *
 * Two viewports: a desktop one and 390 CSS pixels, which is where a table and a
 * <select> escape their container. Overflow is detected by walking the elements and
 * comparing each one's right edge against the document's clientWidth, skipping
 * anything inside an ancestor that scrolls horizontally on purpose. `overflow-x:
 * hidden` on body is never used here: it would hide the defect and make the probe
 * vacuous at the same time.
 *
 * The browser is a shared resource on this machine, so every evaluation first asserts
 * the page identity. A concurrent agent navigating the same instance would otherwise
 * have this script cheerfully measuring somebody else's page.
 */

import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const PAGE = join(ROOT, "docs", "index.html");
const EXPECTED_TITLE = "Nutrition label normalizer";
const LAUNCH_TIMEOUT_MS = Number(process.env.NUTRILABEL_BROWSER_TIMEOUT || 45) * 1000;

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

function findChrome() {
  for (const candidate of CHROME_CANDIDATES) {
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(
    "no Chrome binary found. Install one with `sudo apt-get install google-chrome-stable` " +
      "or set CHROME_PATH. Without it the browser stage cannot run, and a browser stage " +
      "that cannot run is a failure rather than a skip: the unit suite exercises the " +
      "engine but never loads the page, so a page whose script fails to parse would " +
      "still pass everything else."
  );
}

/**
 * Drive Chrome over the DevTools protocol with nothing but the standard library.
 * Adding puppeteer for four evaluations would be a large dependency for a project
 * whose entire engine is stdlib Python.
 */
class Chrome {
  constructor(binary, profileDir) {
    this.binary = binary;
    this.profileDir = profileDir;
    this.messageId = 0;
    this.pending = new Map();
  }

  async start() {
    const args = [
      "--headless=new",
      "--remote-debugging-port=0",
      `--user-data-dir=${this.profileDir}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-extensions",
      "--disable-gpu",
      "--no-sandbox",
      "--allow-file-access-from-files",
      "--hide-scrollbars",
      // Never --disable-crashpad-for-testing or --disable-features=Crashpad. On this
      // machine those two put Chrome into a crash-restart loop that never returns.
      "about:blank",
    ];
    this.process = spawn(this.binary, args, { stdio: ["ignore", "ignore", "pipe"] });

    const endpoint = await new Promise((resolveEndpoint, rejectEndpoint) => {
      let buffer = "";
      const timer = setTimeout(() => {
        rejectEndpoint(new Error(`Chrome did not report a DevTools endpoint within ${LAUNCH_TIMEOUT_MS} ms`));
      }, LAUNCH_TIMEOUT_MS);
      this.process.stderr.on("data", (chunk) => {
        buffer += chunk.toString();
        const match = buffer.match(/ws:\/\/[^\s]+/);
        if (match) {
          clearTimeout(timer);
          resolveEndpoint(match[0]);
        }
      });
      this.process.on("exit", (code) => {
        clearTimeout(timer);
        rejectEndpoint(new Error(`Chrome exited with code ${code} before starting.\n${buffer}`));
      });
    });

    const httpBase = endpoint.replace(/^ws:\/\/([^/]+).*$/, "http://$1");
    const list = await fetch(`${httpBase}/json/new?about:blank`, { method: "PUT" }).then((r) => r.json());
    this.socket = new WebSocket(list.webSocketDebuggerUrl);
    await new Promise((ready, failed) => {
      this.socket.addEventListener("open", ready, { once: true });
      this.socket.addEventListener("error", () => failed(new Error("DevTools socket failed")), { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      const waiting = this.pending.get(message.id);
      if (waiting) {
        this.pending.delete(message.id);
        if (message.error) waiting.reject(new Error(JSON.stringify(message.error)));
        else waiting.resolve(message.result);
      }
    });
  }

  send(method, params = {}) {
    const id = ++this.messageId;
    return new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(
        () => rejectPromise(new Error(`${method} timed out after ${LAUNCH_TIMEOUT_MS} ms`)),
        LAUNCH_TIMEOUT_MS
      );
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolvePromise(value); },
        reject: (error) => { clearTimeout(timer); rejectPromise(error); },
      });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  /** Navigate and measure as one step, with the identity assertion inside it. */
  async loadAndEvaluate(url, width, height, expression) {
    await this.send("Emulation.setDeviceMetricsOverride", {
      width, height, deviceScaleFactor: 1, mobile: width < 500,
    });
    await this.send("Page.enable");
    await this.send("Page.navigate", { url });
    // The page's script sets window.__nutrilabelReady synchronously at the end of its
    // IIFE, so polling for it also proves the whole script parsed and ran. A page whose
    // script has an unbalanced parenthesis renders as static HTML and never sets it.
    const guard = `
      (async () => {
        const deadline = Date.now() + 20000;
        while (!window.__nutrilabelReady) {
          if (Date.now() > deadline) throw new Error("page script never signalled ready");
          await new Promise((r) => setTimeout(r, 50));
        }
        if (document.title !== ${JSON.stringify(EXPECTED_TITLE)}) {
          throw new Error("wrong page loaded: title is " + JSON.stringify(document.title)
            + ". The browser is shared between agents and something navigated it away.");
        }
        return (${expression})();
      })()
    `;
    const result = await this.send("Runtime.evaluate", {
      expression: guard, awaitPromise: true, returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(
        "page evaluation threw: " +
          (result.exceptionDetails.exception?.description || JSON.stringify(result.exceptionDetails))
      );
    }
    return result.result.value;
  }

  async evaluate(expression) {
    const guard = `
      (async () => {
        if (document.title !== ${JSON.stringify(EXPECTED_TITLE)}) {
          throw new Error("wrong page: " + document.title);
        }
        return (${expression})();
      })()
    `;
    const result = await this.send("Runtime.evaluate", {
      expression: guard, awaitPromise: true, returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(
        "page evaluation threw: " +
          (result.exceptionDetails.exception?.description || JSON.stringify(result.exceptionDetails))
      );
    }
    return result.result.value;
  }

  async stop() {
    try { this.socket?.close(); } catch { /* already gone */ }
    if (this.process && this.process.exitCode === null) {
      this.process.kill("SIGTERM");
      await new Promise((done) => {
        const timer = setTimeout(() => { this.process.kill("SIGKILL"); done(); }, 5000);
        this.process.on("exit", () => { clearTimeout(timer); done(); });
      });
    }
  }
}

const OVERFLOW_PROBE = `() => {
  const limit = document.documentElement.clientWidth;
  const offenders = [];
  const scrollers = new Set();
  document.querySelectorAll("*").forEach((node) => {
    const style = getComputedStyle(node);
    if (style.overflowX === "auto" || style.overflowX === "scroll") scrollers.add(node);
  });
  const insideScroller = (node) => {
    for (let p = node.parentElement; p; p = p.parentElement) {
      if (scrollers.has(p)) return true;
    }
    return false;
  };
  document.querySelectorAll("body *").forEach((node) => {
    if (insideScroller(node)) return;
    const rect = node.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return;
    if (rect.right > limit + 0.5) {
      offenders.push({
        tag: node.tagName.toLowerCase(),
        id: node.id || null,
        cls: node.className && typeof node.className === "string" ? node.className : null,
        right: Math.round(rect.right * 100) / 100,
      });
    }
  });
  return {
    limit,
    documentScrollWidth: document.documentElement.scrollWidth,
    bodyOverflowX: getComputedStyle(document.body).overflowX,
    offenders: offenders.slice(0, 8),
  };
}`;

const READ_STATE = `() => {
  const cellText = (nutrient, side) => {
    const row = document.querySelector('#rows tr[data-nutrient="' + nutrient + '"]');
    if (!row) return null;
    const cell = row.querySelector('td[data-side="' + side + '"]');
    return cell ? cell.textContent.trim() : null;
  };
  return {
    banner: document.getElementById("basis-banner").textContent.trim(),
    detail: document.getElementById("basis-detail").textContent.trim(),
    headA: document.getElementById("head-a").textContent.trim(),
    headB: document.getElementById("head-b").textContent.trim(),
    rowCount: document.querySelectorAll("#rows tr").length,
    energyA: cellText("energy_kcal", "a"),
    energyB: cellText("energy_kcal", "b"),
    transA: cellText("trans_fat", "a"),
    findingCards: document.querySelectorAll("#findings .card").length,
    findingLines: document.querySelectorAll("#findings .finding").length,
    pressed: Array.from(document.querySelectorAll("#basis-row button"))
      .filter((b) => b.getAttribute("aria-pressed") === "true")
      .map((b) => b.dataset.basis),
  };
}`;

const failures = [];
function check(name, condition, detail) {
  if (condition) {
    console.log(`  ok   ${name}`);
  } else {
    console.log(`  FAIL ${name}: ${detail}`);
    failures.push(`${name}: ${detail}`);
  }
}

async function main() {
  if (!existsSync(PAGE)) {
    throw new Error(`${PAGE} does not exist. Run: python3 scripts/build_page.py`);
  }
  const binary = findChrome();
  const profile = await mkdtemp(join(tmpdir(), "nutrilabel-chrome-"));
  const chrome = new Chrome(binary, profile);
  const url = `file://${PAGE}`;

  try {
    await chrome.start();
    console.log(`browser: ${binary}`);

    for (const [name, width, height] of [["desktop", 1280, 900], ["narrow", 390, 844]]) {
      console.log(`viewport ${name} (${width}x${height})`);
      const initial = await chrome.loadAndEvaluate(url, width, height, READ_STATE);

      check(`${name}: the script ran and produced rows`, initial.rowCount >= 8,
        `only ${initial.rowCount} rows rendered, so the page is static HTML`);
      check(`${name}: the basis in force is stated`,
        initial.banner.startsWith("Basis in force: per 100 g"),
        `banner reads ${JSON.stringify(initial.banner)}`);
      check(`${name}: cereal A energy is an interval, not a point`,
        /^\d+(\.\d+)? to \d+/.test(initial.energyA || ""),
        `energy cell reads ${JSON.stringify(initial.energyA)}`);
      check(`${name}: exactly one basis button is pressed`,
        initial.pressed.length === 1 && initial.pressed[0] === "per_100g",
        `pressed = ${JSON.stringify(initial.pressed)}`);
      check(`${name}: both findings cards rendered`, initial.findingCards === 2,
        `found ${initial.findingCards} cards`);

      // Per 100 g the two cereals must be close; that is the point of the page.
      const parse = (text) => text.split(" to ").map(Number);
      const [aLo, aHi] = parse(initial.energyA);
      const [bLo, bHi] = parse(initial.energyB);
      check(`${name}: per 100 g the two cereals overlap`, aLo <= bHi && bLo <= aHi,
        `A ${aLo}-${aHi} does not overlap B ${bLo}-${bHi}`);

      const overflow = await chrome.evaluate(OVERFLOW_PROBE);
      check(`${name}: body does not hide horizontal overflow`,
        overflow.bodyOverflowX !== "hidden",
        `body overflow-x is ${overflow.bodyOverflowX}, which would make this probe vacuous`);
      check(`${name}: nothing escapes the viewport horizontally`,
        overflow.offenders.length === 0,
        `${overflow.offenders.length} element(s) past ${overflow.limit}px: ` +
          JSON.stringify(overflow.offenders));
      check(`${name}: the document does not scroll sideways`,
        overflow.documentScrollWidth <= overflow.limit + 1,
        `scrollWidth ${overflow.documentScrollWidth} against clientWidth ${overflow.limit}`);

      // Switching the basis must change the numbers, not just the label. A control
      // that relabels without recomputing is exactly the bug this page could have.
      const switched = await chrome.evaluate(`() => {
        document.getElementById("basis-per_serving").click();
        return (${READ_STATE})();
      }`);
      check(`${name}: the basis switch changes the stated basis`,
        switched.banner.includes("per 34 g serving") && switched.banner.includes("per 55 g serving"),
        `banner reads ${JSON.stringify(switched.banner)}`);
      check(`${name}: the basis switch changes the numbers`,
        switched.energyA !== initial.energyA && switched.energyB !== initial.energyB,
        `energy stayed at ${initial.energyA} / ${initial.energyB}`);
      const [sLo] = parse(switched.energyA);
      const [tLo] = parse(switched.energyB);
      check(`${name}: per serving the granola looks far larger`, tLo / sLo > 1.5,
        `ratio of lower bounds is ${(tLo / sLo).toFixed(2)}`);

      const container = await chrome.evaluate(`() => {
        document.getElementById("pick-a").value = "us-crackers-2point5-servings";
        document.getElementById("pick-a").dispatchEvent(new Event("change"));
        document.getElementById("basis-per_container").click();
        return (${READ_STATE})();
      }`);
      check(`${name}: changing the product changes the column heading`,
        container.headA.includes("crackers"), `heading reads ${JSON.stringify(container.headA)}`);
      check(`${name}: a declared 0 g trans fat reaches past a gram per container`,
        container.transA === "0 to 1.38",
        `per-container trans fat reads ${JSON.stringify(container.transA)}`);
      check(`${name}: the compounding warning is shown`,
        container.detail.includes("compounds"),
        `detail reads ${JSON.stringify(container.detail.slice(0, 160))}`);

      const broken = await chrome.evaluate(`() => {
        document.getElementById("pick-b").value = "broken-energy-mismatch";
        document.getElementById("pick-b").dispatchEvent(new Event("change"));
        return {
          flags: Array.from(document.querySelectorAll("#findings .finding.flag"))
            .map((n) => n.textContent.trim()),
        };
      }`);
      check(`${name}: a self-contradicting label shows a flag`,
        broken.flags.some((text) => text.includes("INCONSISTENT")),
        `flags rendered: ${JSON.stringify(broken.flags)}`);
    }
  } finally {
    await chrome.stop();
    await rm(profile, { recursive: true, force: true });
  }

  if (failures.length) {
    console.error(`\nbrowser: ${failures.length} check(s) failed`);
    process.exit(1);
  }
  console.log("\nbrowser: all page checks passed at 1280px and 390px");
}

main().catch((error) => {
  console.error(`browser: ${error.message}`);
  process.exit(1);
});
