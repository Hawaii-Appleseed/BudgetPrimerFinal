// Renders one page at one viewport width and prints what fidelity.py compares:
// the visible text, every distinct text style, any sideways overflow — and a
// full-page screenshot written to argv[4]. Run by docsync/fidelity.py.
//   node fidelity_probe.js <file-or-url> <width> <screenshot.png>
const path = require("path");
const root = process.env.CIDER_ROOT || path.resolve(__dirname, "..");
const { chromium } = require(require.resolve("playwright", { paths: [root] }));
(async () => {
  const [url, width, shot] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: +width, height: 900 } });
  await p.goto(url, { waitUntil: "load", timeout: 45000 });
  await p.evaluate(() => document.fonts && document.fonts.ready);
  await p.waitForTimeout(400);
  const info = await p.evaluate(() => {
    const styles = new Set();
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (!n.nodeValue.trim()) continue;
      const e = n.parentElement;
      if (!e || ["SCRIPT", "STYLE", "NOSCRIPT"].includes(e.tagName)) continue;
      const c = getComputedStyle(e);
      // The nearest classed ancestor says WHERE the text is, so the same
      // look in the wrong place still counts as a difference.
      let a = e; while (a && !a.classList.length) a = a.parentElement;
      styles.add([a ? a.className : "", c.fontFamily, c.fontSize, c.fontWeight, c.fontStyle,
                  c.color, c.textTransform, c.textDecorationLine, c.letterSpacing].join(" | "));
    }
    return {
      text: document.body.innerText.replace(/\s+/g, " ").trim(),
      styles: [...styles].sort(),
      scrollWidth: document.documentElement.scrollWidth,
      height: document.documentElement.scrollHeight,
    };
  });
  await p.screenshot({ path: shot, fullPage: true });
  await b.close();
  console.log(JSON.stringify(info));
})().catch((e) => { console.error(e.message); process.exit(1); });
