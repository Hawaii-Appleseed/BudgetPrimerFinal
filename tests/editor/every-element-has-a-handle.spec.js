// Every visible thing on a report has a handle the editor can use.
//
// A copy of primer-editor's tests/editor/every-element-has-a-handle.spec.js
// (not vendored: specs are not engine files). Keep the two in step.
//
// The promise (report-editor skill): all text types, every text box moves and
// resizes, every graphic and every chart can be picked up. docsync.check holds
// reports to it by READING THE MARKUP — which is the right tool for most of it
// and blind to three things that only exist once a browser draws the page:
//
//   * a surface painted by CSS — a card's background, a band, a rule, a
//     bordered panel. Nothing in the markup says a <div class="group"> is a
//     visible card; the stylesheet does. The audit that wrote this file found
//     cards, bands and page-furniture strips on half the bound reports that
//     nothing could move, resize or recolour.
//   * words drawn by CSS (::before / ::after content) — no slot can reach them.
//   * text and pictures a page's own script puts there after load.
//
// So this spec builds each strict report's EDIT-mode draft (the page the
// editor shows, with its hooks), opens it, and looks at what was drawn:
//
//   text     — typed through a slot, derived (C.derived), chart text, or an
//              editor-owned box; and movable (data-el on it or above it)
//   graphic  — an <img>/<svg>/<video>/<canvas>/<iframe> on the page is
//              movable, unless it is a glyph inside a link or button
//   surface  — a painted box (background, border, shadow, <hr>) is movable,
//              or is a band (data-sec) or a recolourable surface (data-fill)
//   frame    — a movable that holds movables is positioned (L.frame)
//   css text — no words come from ::before/::after
//
// The exceptions a binding already declares in docsync.yml (editability_ok)
// hold here too, by the same exact string docsync.check uses. What is known
// and not yet fixed is listed in KNOWN below — shrink-only, as in
// text-legibility.spec.js: anything not named fails, and a named item that is
// gone fails too until its name is deleted. NEVER add a name to silence a new
// finding; give the thing a handle.
//
// It drives no editor code, so tools/affected.mjs cannot see what it runs:
// affected-by: docsync/layout.py docsync/blocks.py docsync/content.py docsync/new.py docsync.yml projects/**
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const REPO = path.resolve(__dirname, '../..');

// docsync.yml by regex, like text-legibility.spec.js (no yaml dependency).
const discover = () => {
  const yml = fs.readFileSync(path.join(REPO, 'docsync.yml'), 'utf8');
  const ids = [...yml.matchAll(/^ {2}- id: (\S+)$/gm)];
  return ids.map((m, i) => {
    const block = yml.slice(m.index, i + 1 < ids.length ? ids[i + 1].index : undefined);
    const render = (block.match(/^ {6}render: (\S+)$/m) || [])[1];
    const out = (block.match(/^ {6}out: (\S+)$/m) || [])[1];
    const strict = /^ {4}editability: strict$/m.test(block);
    const okBlock = (block.match(/^ {4}editability_ok:\n((?: {6}(?:- .*|#.*)\n)+)/m) || [])[1] || '';
    const ok = [...okBlock.matchAll(/^ {6}- "(.*)"$/gm)].map(x => x[1].replace(/\\"/g, '"'));
    return { id: m[1], render, out, strict, ok };
  }).filter(r => r.render && r.out && r.strict);
};

const REPORTS = discover();

// Known, not yet fixed: report -> the exact findings, as report() prints them.
// Empty since 2026-09-24, when the audit that wrote this file found 35 painted
// surfaces with no handle and every one was given the handle that fits it
// (L.frame for a card, list, row or strip; L.sec for a band). Keep it empty:
// see the report-editor skill, "Every surface has a handle".
const KNOWN = new Map([
]);

// Build the edit-mode draft beside the report's own output, so relative
// assets resolve as they do in the editor. One file per report and run.
const draftFor = (r) => {
  const file = path.join(REPO, path.dirname(r.out), `__handles-${process.pid}.html`);
  const env = Object.fromEntries(Object.entries(process.env)
    .filter(([k]) => !k.startsWith('DOCSYNC_')));
  execFileSync('python3', [path.join(REPO, r.render)], {
    cwd: REPO, env: { ...env, DOCSYNC_EDIT: '1', DOCSYNC_OUT: file }, stdio: 'pipe',
  });
  return file;
};

// Everything on the page without a handle, as signature strings.
async function findings(page) {
  return page.evaluate(() => {
    const MOVE = '[data-el],[data-shape],[data-chart]';
    const TYPE = '[data-slot],[data-fixed],[data-ch],[data-desc],[data-restates],'
      + '[data-el^="text."],[data-el^="table."],[data-el^="endnote."]';
    // blocks.is_data_mark: no letters, or one run of at most three
    const dataMark = t => {
      const w = t.match(/[A-Za-zÀ-ɏʻ‘]+/g) || [];
      return !w.length || (w.length === 1 && w[0].length <= 3);
    };
    const shown = el => {
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return false;
      const r = el.getBoundingClientRect();
      return r.width > 3 && r.height > 3;
    };
    const cls = el => (typeof el.className === 'string' && el.className.trim()
      ? el.className.trim().split(/\s+/)[0] : '');
    const sig = el => {
      const own = el.tagName.toLowerCase() + (cls(el) ? '.' + cls(el) : '');
      let p = el.parentElement;
      while (p && !cls(p)) p = p.parentElement;
      return p && !p.matches('.page') ? `${own} in .${cls(p)}` : own;
    };
    const pages = [...document.querySelectorAll('section.page, .page')];
    const onPage = el => pages.some(pg => pg !== el && pg.contains(el));
    const out = new Set();
    // text
    const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n; (n = tw.nextNode());) {
      const t = n.textContent.replace(/\s+/g, ' ').trim();
      const el = n.parentElement;
      if (!t || !el || !onPage(el) || el.closest('style,script,title,defs')) continue;
      if (dataMark(t) || !shown(el)) continue;
      if (!el.closest(TYPE)) out.add(`text: ${t}`);
      else if (!el.closest(MOVE)) out.add(`immovable text: ${t}`);
    }
    // graphics
    for (const el of document.querySelectorAll('img,svg,video,canvas,iframe,object')) {
      if (!onPage(el) || !shown(el)) continue;
      if (el.tagName === 'svg' && el.parentElement.closest('svg')) continue;
      if (el.matches('.shape-layer') || el.closest(MOVE) || el.closest('a,button')) continue;
      let p = el.parentElement;
      while (p && !cls(p)) p = p.parentElement;
      const name = el.getAttribute('alt') || el.getAttribute('aria-label')
        || (el.getAttribute('src') || '').split('/').pop()
        || (cls(el) ? `${el.tagName.toLowerCase()}.${cls(el)}`
          : p ? `${el.tagName.toLowerCase()} in .${cls(p)}` : el.tagName.toLowerCase());
      out.add(`graphic: ${name}`);
    }
    // painted surfaces
    for (const el of document.querySelectorAll('.page *')) {
      if (el.closest('svg') || !shown(el) || el.closest(MOVE)) continue;
      if (el.hasAttribute('data-sec') || el.hasAttribute('data-fill')) continue;
      // Hidden decoration drawn inside a band (its background layers, a
      // progress dot) is the band's own drawing, handled by the band's grip.
      if (el.closest('[aria-hidden="true"]') && el.closest('[data-sec]')) continue;
      const cs = getComputedStyle(el);
      if (cs.display === 'inline') continue;          // text styling: text rules cover it
      // A link's or button's own surface belongs to the control (a carousel
      // arrow, a Donate button), as a glyph inside one does; its words are
      // judged by the text rules like any others.
      if (el.closest('a, button')) continue;
      const r = el.getBoundingClientRect();
      const bg = cs.backgroundColor;
      const painted = (!/rgba\(0, 0, 0, 0\)|transparent/.test(bg))
        || cs.backgroundImage !== 'none' || el.tagName === 'HR'
        || (cs.boxShadow && cs.boxShadow !== 'none')
        || ['Top', 'Right', 'Bottom', 'Left'].some(s =>
          parseFloat(cs[`border${s}Width`]) > 0 && cs[`border${s}Style`] !== 'none'
          && !/rgba\(0, 0, 0, 0\)/.test(cs[`border${s}Color`]));
      if (!painted) continue;
      // A key mark beside its words (a legend swatch) belongs to that entry.
      if (r.width <= 20 && r.height <= 20 && el.parentElement
          && el.parentElement.querySelector(TYPE)) continue;
      out.add(`surface: ${sig(el)}`);
    }
    // a container holding movables must be a positioned frame (L.frame):
    // a field moved inside a static one saves coordinates that re-base the
    // moment the container is first dragged, and the words leap.
    for (const el of document.querySelectorAll('.page [data-el]')) {
      if (!el.querySelector('[data-el]') || !shown(el)) continue;
      if (el.matches('.ds-graphic, .ds-textbox')) continue;
      if (getComputedStyle(el).position === 'static') out.add(`static frame: ${sig(el)}`);
    }
    // words drawn by CSS
    for (const el of document.querySelectorAll('.page *')) {
      if (!shown(el)) continue;
      for (const ps of ['::before', '::after']) {
        const c = getComputedStyle(el, ps).content;
        if (!c || c === 'none' || c === 'normal') continue;
        const lit = (c.match(/"([^"]*)"/g) || []).map(s => s.slice(1, -1)).join('');
        if (/[A-Za-z]{2}/.test(lit)) out.add(`css text: ${sig(el)}${ps} ${lit}`);
      }
    }
    return [...out].sort();
  });
}

test('every strict report was discovered', () => {
  // This repo binds the primer alone (primer-editor's copy expects more than five).
  expect(REPORTS.length, REPORTS.map(r => r.id).join(', ')).toBeGreaterThan(0);
});

for (const rep of REPORTS) {
  test(`${rep.id}: every visible thing has a handle`, async ({ page }) => {
    const file = draftFor(rep);
    try {
      await page.setViewportSize({ width: 1400, height: 1000 });
      await page.goto(`file://${file}`);
      await page.locator('.page').first().waitFor();
      await page.waitForTimeout(500);
      const ok = new Set(rep.ok);
      const all = (await findings(page)).filter(f => !ok.has(f.replace(/^[^:]+: /, '')));
      const known = KNOWN.get(rep.id) || [];
      const fresh = all.filter(f => !known.includes(f));
      const gone = known.filter(f => !all.includes(f));
      expect(fresh, `${rep.id}: no handle — give each one (report-editor skill)`).toEqual([]);
      expect(gone, `${rep.id}: fixed — delete these from KNOWN`).toEqual([]);
    } finally {
      fs.rmSync(file, { force: true });
    }
  });
}
