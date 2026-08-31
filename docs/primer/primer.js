/* Single-page isolation for per-page PNG export. The live server's export
   endpoint screenshots "file://…/__export.html?only=N" once per page; this
   hides every page but the Nth and squares it to the viewport top-left so the
   capture is exactly that trim page. Inert without ?only= — normal loads never
   see it. */
(function () {
  var m = /[?&]only=(\d+)/.exec(location.search);
  if (!m) return;
  var n = parseInt(m[1], 10);
  document.documentElement.style.background = document.body.style.background = '#fff';
  var tb = document.querySelector('.toolbar');
  if (tb) tb.style.display = 'none';
  document.querySelectorAll('section.page').forEach(function (pg, i) {
    if (i === n - 1) { pg.style.margin = '0'; pg.style.height = '11in'; pg.style.boxShadow = 'none'; }
    else { pg.style.display = 'none'; }
  });
})();

/* Interactive layer: hover tooltips + department preview popovers that surface a
   slice of the Budget Tracker (funding trend + largest programs) in place.
   Progressive enhancement over the inline SVG; print unaffected. */
(function () {
  var tip = document.getElementById('tip');

  // ---- hover tooltips ----
  if (tip) {
    document.addEventListener('mousemove', function (e) {
      var el = e.target.closest ? e.target.closest('.iv') : null;
      if (el && el.dataset.tip) {
        tip.textContent = el.dataset.tip;
        tip.style.opacity = 1;
        var x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
        tip.style.left = x + 'px';
        tip.style.top = (e.clientY + 16) + 'px';
      } else {
        tip.style.opacity = 0;
      }
    });
  }

  // ---- fiscal-year pickers (Figures 3/4/5) ----
  // Each <select.fy-pick data-fig> toggles the sibling [data-fig][data-fy]
  // elements (pie SVGs + year-specific paragraphs) to the chosen year.
  document.querySelectorAll('select.fy-pick').forEach(function (sel) {
    sel.addEventListener('change', function () {
      var fig = sel.dataset.fig, yr = sel.value;
      // toggle the attribute (not the .hidden property, which SVG elements lack)
      document.querySelectorAll('[data-fig="' + fig + '"][data-fy]').forEach(function (el) {
        if (el.getAttribute('data-fy') === yr) el.removeAttribute('hidden');
        else el.setAttribute('hidden', '');
      });
    });
  });

  // ---- animated unfold for the expandable panels ----
  // Native <details> snaps open in one frame. Height is animated between the
  // measured extremes with the Web Animations API instead — CSS alone cannot
  // transition to height:auto without features Safari/Firefox lack, and the
  // published page must work everywhere even though the editor app is
  // Chrome-hosted. Speed comes from --expand-dur in primer.css, so it is
  // adjustable in one place without touching this script. Reduced-motion
  // readers keep the native snap: for them the animation IS the regression.
  document.querySelectorAll('details.obligated').forEach(function (d) {
    // Whatever follows the summary IS the panel: .obligated-panel on the
    // obligated-costs one, .cards2 on the appropriations one. Matching a
    // specific class here silently reverted the second panel to the snap.
    var sum = d.querySelector('summary');
    var panel = sum && sum.nextElementSibling;
    if (!panel || !sum) return;
    sum.addEventListener('click', function (e) {
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      var dur = parseFloat(getComputedStyle(document.documentElement)
                  .getPropertyValue('--expand-dur')) * 1000;
      if (!(dur > 0)) return;                    // 0s / unparsable: native snap
      if (panel.getAnimations && panel.getAnimations().length) {
        e.preventDefault(); return;              // mid-animation clicks do nothing
      }
      e.preventDefault();
      panel.style.overflow = 'hidden';
      var done = function (open) {
        return function () { panel.style.overflow = ''; if (!open) d.open = false; };
      };
      if (!d.open) {
        d.open = true;                           // content must exist to measure
        var h = panel.scrollHeight;
        panel.animate(
          { height: ['0px', h + 'px'], opacity: [0, 1] },
          { duration: dur, easing: 'ease' }).onfinish = done(true);
      } else {
        panel.animate(
          { height: [panel.scrollHeight + 'px', '0px'], opacity: [1, 0] },
          { duration: dur, easing: 'ease' }).onfinish = done(false);
      }
    });
  });

  // ---- print the obligated-costs chart on its own page when expanded ----
  // The interactive <details> is noprint; on print, if it's open, we clone its
  // panel into a dedicated .page inserted after the obligated section so the
  // chart appears in the PDF without overflowing that fixed-height page.
  var obligDetails = document.querySelector('details.obligated');
  if (obligDetails) {
    var obligPrintPage = null;
    var removeObligPrintPage = function () {
      if (obligPrintPage) { obligPrintPage.remove(); obligPrintPage = null; }
    };
    var addObligPrintPage = function () {
      removeObligPrintPage();
      if (!obligDetails.open) return;
      var panel = obligDetails.querySelector('.obligated-panel');
      var page = obligDetails.closest('.page');
      if (!panel || !page || !page.parentNode) return;
      obligPrintPage = document.createElement('section');
      obligPrintPage.className = 'page obligated-print-page';
      var h = document.createElement('h2');
      h.className = 'sub';
      h.textContent = 'Obligated Costs';
      obligPrintPage.appendChild(h);
      obligPrintPage.appendChild(panel.cloneNode(true));
      page.parentNode.insertBefore(obligPrintPage, page.nextSibling);
    };
    window.addEventListener('beforeprint', addObligPrintPage);
    window.addEventListener('afterprint', removeObligPrintPage);
  }

  // ---- department preview popover ----
  var LINKS = window.PRIMER_LINKS || {};
  var SPAN = window.PRIMER_FY_SPAN || [2016, 2027];
  var pop = document.createElement('div');
  pop.id = 'popover';
  pop.className = 'noprint';
  pop.setAttribute('role', 'dialog');
  pop.hidden = true;
  document.body.appendChild(pop);

  function money(n) {
    if (!n) return '$0';
    if (n >= 1e9) return '$' + (n / 1e9).toFixed(2).replace(/\.?0+$/, '') + 'B';
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    return '$' + Math.round(n / 1e3) + 'K';
  }

  // sparkline of $M values -> inline SVG (area + line + endpoint dot)
  function spark(vals) {
    var W = 150, H = 40, P = 3;
    var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
    var rng = (mx - mn) || 1;
    var pts = vals.map(function (v, i) {
      return [(P + i * (W - 2 * P) / (vals.length - 1)).toFixed(1),
              (H - P - (v - mn) / rng * (H - 2 * P)).toFixed(1)];
    });
    var line = pts.map(function (p) { return p.join(','); }).join(' ');
    var area = line + ' ' + pts[pts.length - 1][0] + ',' + (H - 1) + ' ' + pts[0][0] + ',' + (H - 1);
    var last = pts[pts.length - 1];
    return '<svg class="spark" viewBox="0 0 ' + W + ' ' + H + '" aria-hidden="true">' +
      '<polygon points="' + area + '" fill="rgba(107,144,128,.18)"/>' +
      '<polyline points="' + line + '" fill="none" stroke="#6b9080" stroke-width="1.8"/>' +
      '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="2.6" fill="#2d6a4f"/></svg>';
  }

  function preview(d) {
    var h = '';
    if (d.hist && d.hist.length > 1) {
      var first = d.hist[0], lastv = d.hist[d.hist.length - 1];
      var pct = first ? Math.round((lastv - first) / first * 100) : 0;
      h += '<div class="pop-sec"><div class="pop-sec-h">Funding, FY' +
        String(SPAN[0]).slice(2) + '–FY' + String(SPAN[1]).slice(2) + '</div>' +
        '<div class="pop-spark">' + spark(d.hist) +
        '<div class="pop-spark-meta"><b>' + money(lastv * 1e6) + '</b><span>' +
        (pct >= 0 ? '+' : '') + pct + '% since FY' + String(SPAN[0]).slice(2) +
        '</span></div></div></div>';
    }
    if (d.programs && d.programs.length) {
      var max = d.programs[0][1] || 1;
      h += '<div class="pop-sec"><div class="pop-sec-h">Largest programs, FY27</div>';
      d.programs.forEach(function (p) {
        var nm = p[0].length > 34 ? p[0].slice(0, 33) + '…' : p[0];
        h += '<div class="pop-prog"><div class="pop-prog-t"><span>' + nm +
          '</span><b>' + money(p[1]) + '</b></div>' +
          '<div class="pop-bar"><i style="width:' +
          Math.max(100 * p[1] / max, 2).toFixed(1) + '%"></i></div></div>';
      });
      if (d.nprogs > d.programs.length) {
        h += '<div class="pop-more">+ ' + (d.nprogs - d.programs.length) +
          ' more programs in the tracker</div>';
      }
      h += '</div>';
    }
    return h;
  }

  function show(code, anchor) {
    var d = LINKS[code];
    if (!d) return;
    var stats = '';
    if (d.operating) stats += '<div><span>Operating</span><b>' + money(d.operating) + '</b></div>';
    if (d.capital) stats += '<div><span>Capital</span><b>' + money(d.capital) + '</b></div>';
    if (d.positions) stats += '<div><span>Positions</span><b>' +
      d.positions.toLocaleString() + '</b></div>';
    pop.innerHTML =
      '<button class="pop-x" aria-label="Close">×</button>' +
      '<h5>' + d.name + '</h5>' +
      (d.blurb && d.blurb !== '.' ? '<p class="pop-blurb">' + d.blurb + '</p>' : '') +
      (stats ? '<div class="pop-stats">' + stats + '</div>' : '') +
      preview(d) +
      '<a class="pop-cta" href="' + d.url + '" target="_blank" rel="noopener">' +
      'Open in Budget Tracker →</a>';
    pop.hidden = false;
    var r = anchor.getBoundingClientRect();
    var pw = pop.offsetWidth, ph = pop.offsetHeight;
    var x = Math.min(Math.max(r.left + r.width / 2 - pw / 2, 10),
                     window.innerWidth - pw - 10);
    var y = r.bottom + 10;
    if (y + ph > window.innerHeight - 10) y = Math.max(r.top - ph - 10, 10);
    pop.style.left = x + 'px';
    pop.style.top = y + 'px';
    pop.querySelector('.pop-x').focus();
  }

  function hide() { pop.hidden = true; }

  document.addEventListener('click', function (e) {
    if (!pop.hidden && pop.contains(e.target)) {
      if (e.target.classList.contains('pop-x')) hide();
      return;
    }
    var el = e.target.closest ? e.target.closest('[data-dept]') : null;
    if (el) {
      if (tip) tip.style.opacity = 0;
      show(el.dataset.dept, el);
    } else {
      hide();
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') hide();
  });
  window.addEventListener('scroll', hide, {passive: true});

  // ---- footnote popovers ----
  var NOTES = window.PRIMER_NOTES || [];
  var fnp = document.createElement('div');
  fnp.id = 'fnpop';
  fnp.className = 'noprint';
  fnp.hidden = true;
  document.body.appendChild(fnp);
  var fnPinned = false, fnTimer = null;

  function fnHide() { fnp.hidden = true; fnPinned = false; }

  function fnShow(n, anchor) {
    var note = NOTES[n - 1];
    if (!note) return;
    var host = '';
    try { host = new URL(note.u).hostname.replace(/^www\./, ''); } catch (err) { host = 'source'; }
    fnp.innerHTML = '<div class="fn-n">Note ' + n + '</div>' +
      '<p>' + note.t + '</p>' +
      '<a class="fn-cta" href="' + note.u + '" target="_blank" rel="noopener">' + host + ' ↗</a>';
    fnp.hidden = false;
    var r = anchor.getBoundingClientRect();
    var pw = fnp.offsetWidth, ph = fnp.offsetHeight;
    var x = Math.min(Math.max(r.left + r.width / 2 - pw / 2, 10), window.innerWidth - pw - 10);
    var y = r.bottom + 8;
    if (y + ph > window.innerHeight - 10) y = Math.max(r.top - ph - 8, 10);
    fnp.style.left = x + 'px';
    fnp.style.top = y + 'px';
  }

  document.addEventListener('mouseover', function (e) {
    var a = e.target.closest ? e.target.closest('a.fn') : null;
    if (a) {
      clearTimeout(fnTimer);
      fnShow(+a.dataset.fn, a);
    } else if (e.target.closest && e.target.closest('#fnpop')) {
      clearTimeout(fnTimer);   // inside the popover — keep it open so the link is reachable
    } else if (!fnPinned && !fnp.hidden) {
      clearTimeout(fnTimer);
      fnTimer = setTimeout(fnHide, 250);   // grace period to reach the popover
    }
  });

  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a.fn') : null;
    if (a) {
      e.preventDefault();                  // stay on the page; the link is in the popover
      fnShow(+a.dataset.fn, a);
      fnPinned = true;
      return;
    }
    if (!(e.target.closest && e.target.closest('#fnpop'))) fnHide();
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') fnHide(); });
  window.addEventListener('scroll', fnHide, {passive: true});
})();

/* The lifecycle wheel opens centred in its scroller.

   chart_scroll() gives the wheel a min-width so it scrolls on a phone rather
   than shrinking its labels away, and a scroller starts flush-left — which put
   DEC through APR on screen and left MAY through NOV to be discovered by
   swiping. The wheel is symmetric and reads as a whole, so an even clip on both
   sides shows more of it at a glance, and both edge shadows are then visible to
   say it scrolls at all.

   Scoped to this figure on purpose: centring is right for a disc, and wrong for
   the bar charts, whose labels run down their left edge.

   A reader who scrolls it themselves owns the position from then on, rotation
   included. That is tested by comparing against the offset we last set rather
   than by listening for scroll events — a scroll listener cannot tell the
   reader's swipe from the browser echoing our own assignment back. */
(function () {
  var sc = document.querySelector('.lifecycle-wrap .ds-chart-scroll');
  if (!sc) return;
  var ours = null;                 // the offset we put there, or null before we have

  function centre() {
    var mid = (sc.scrollWidth - sc.clientWidth) / 2;
    if (mid <= 0) return;                                   // it fits; nothing to centre
    if (ours !== null && Math.abs(sc.scrollLeft - ours) > 2) return;   // reader moved it
    sc.scrollLeft = ours = mid;
  }

  centre();
  window.addEventListener('resize', centre);
})();
