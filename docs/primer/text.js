// Copy one page of the report as plain text. Flattens the data rows and
// legends first, so what lands on the clipboard reads as prose and figures
// rather than a column of orphaned labels and values.
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.copy');
  if (!btn) return;
  var body = btn.closest('.page').querySelector('.page-b');
  // Flatten the data rows and legends so a copied page reads as prose+figures
  // rather than a column of orphaned labels and values.
  var clone = body.cloneNode(true);
  clone.querySelectorAll('.drow').forEach(function (row) {
    var dt = row.querySelector('dt'), dd = row.querySelector('dd');
    var line = document.createElement('p');
    line.style.margin = '0';
    line.textContent = dd && dd.textContent.trim()
      ? dt.textContent.trim() + ': ' + dd.textContent.replace(/\s+/g, ' ').trim()
      : dt.textContent.trim();
    row.replaceWith(line);
  });
  clone.querySelectorAll('.legend').forEach(function (lg) {
    var keys = Array.prototype.map.call(lg.querySelectorAll('.key'), function (k) {
      return k.textContent.trim();
    });
    var line = document.createElement('p');
    line.style.margin = '0';
    line.textContent = 'Key: ' + keys.join(' | ');
    lg.replaceWith(line);
  });
  // innerText needs a rendered node to resolve line breaks — park it offscreen.
  clone.style.cssText = 'position:fixed;left:-9999px;top:0;width:40rem';
  document.body.appendChild(clone);
  var text = clone.innerText.replace(/\n{3,}/g, '\n\n').trim();
  clone.remove();
  var done = function () {
    btn.textContent = 'Copied';
    btn.dataset.done = '1';
    setTimeout(function () {
      btn.textContent = 'Copy page text';
      delete btn.dataset.done;
    }, 1800);
  };
  var fallback = function () {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    document.body.removeChild(ta);
    if (ok) { done(); return; }
    var r = document.createRange();
    r.selectNodeContents(body);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
    btn.textContent = 'Selected — press ⌘C';
    setTimeout(function () { btn.textContent = 'Copy page text'; }, 2600);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, fallback);
  } else {
    fallback();
  }
});
