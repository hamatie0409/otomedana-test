/* トップページの検索と絞り込み。
   index.json だけで動き、検索欄に触れたとき suggest.json、
   キャラ属性で絞るときだけ traits.json を追加で読む。 */
(function () {
  var BP = window.BASE_PATH || '';
  var root = document.getElementById('app');
  if (!root) return;

  var DATA = null, TRAITS = null, SUGGEST = null;
  var TODAY = new Date().toISOString().slice(0, 10);

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    for (var k in (attrs || {})) {
      if (k === 'text') n.textContent = attrs[k];
      else n.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { n.appendChild(c); });
    return n;
  }
  function option(v, t) { var o = el('option', { value: v }); o.textContent = t; return o; }
  function val(id) { var n = document.getElementById(id); return n ? n.value : ''; }
  function norm(s) { return (s || '').toLowerCase().replace(/[\s　]/g, ''); }

  /* ---------------- 組み立て ---------------- */

  function build() {
    var panel = el('div', { class: 'search-panel' });
    panel.appendChild(el('h2', { text: '乙女ゲームを探す', class: 'sp-title' }));

    // 検索対象（otomex に倣った切り替え）
    var modes = [['all', 'すべて'], ['title', '作品名'], ['cv', '声優名'], ['char', 'キャラクター名']];
    var mrow = el('div', { class: 'sp-modes' });
    modes.forEach(function (m, i) {
      var lab = el('label', { class: 'radio' });
      var r = el('input', { type: 'radio', name: 'smode', value: m[0], id: 'm-' + m[0] });
      if (i === 0) r.checked = true;
      r.addEventListener('change', function () { onType(); render(); });
      lab.appendChild(r);
      lab.appendChild(document.createTextNode(m[1]));
      mrow.appendChild(lab);
    });
    panel.appendChild(mrow);

    // 検索欄＋候補
    var box = el('div', { class: 'sp-box' });
    var q = el('input', { type: 'search', id: 'f-q', autocomplete: 'off',
                          placeholder: '作品名・声優名・キャラクター名を入力' });
    var sug = el('ul', { class: 'sp-sug', id: 'f-sug', hidden: 'hidden' });
    box.appendChild(q);
    box.appendChild(sug);
    panel.appendChild(box);

    // よく使う絞り込み
    var row = el('div', { class: 'filters' });
    var pl = el('select', { id: 'f-plat' });
    pl.appendChild(option('', '機種：すべて'));
    var groups = {};
    (DATA.vocab.platform || []).forEach(function (p, i) {
      var g = p.g || 'その他';
      if (!groups[g]) {
        groups[g] = el('optgroup', { label: g });
        pl.appendChild(groups[g]);
      }
      groups[g].appendChild(option(i, p.n));
    });
    var se = el('select', { id: 'f-series' }); se.appendChild(option('', 'シリーズ：すべて'));
    (DATA.vocab.series || []).forEach(function (p, i) {
      se.appendChild(option(i, p.n + '（' + p.c + '作品）'));
    });
    var yr = el('select', { id: 'f-year' }); yr.appendChild(option('', '発売年：すべて'));
    ['2021-', '2016-2020', '2011-2015', '-2010'].forEach(function (y) { yr.appendChild(option(y, y)); });
    var st = el('select', { id: 'f-sort' });
    [['new', '発売日が新しい順'], ['old', '発売日が古い順'],
     ['rate', '評価が高い順'], ['pop', '票数が多い順']].forEach(function (o) {
      st.appendChild(option(o[0], o[1]));
    });
    [pl, se, yr, st].forEach(function (x) { row.appendChild(x); });
    panel.appendChild(row);

    // 詳しく絞り込む（普段は閉じておく）
    var det = el('details', { class: 'sp-more' });
    det.appendChild(el('summary', { text: '詳しく絞り込む' }));
    var row2 = el('div', { class: 'filters' });
    var cv = el('select', { id: 'f-cv' }); cv.appendChild(option('', '声優：すべて'));
    (DATA.vocab.cv || []).forEach(function (p, i) { cv.appendChild(option(i, p.n)); });
    var sf = el('select', { id: 'f-staff' }); sf.appendChild(option('', 'スタッフ：すべて'));
    (DATA.vocab.staff || []).forEach(function (p, i) {
      sf.appendChild(option(i, p.n + (p.r ? '（' + p.r + '）' : '')));
    });
    var pb = el('select', { id: 'f-pub' }); pb.appendChild(option('', '発売元：すべて'));
    (DATA.vocab.publisher || []).forEach(function (p, i) { pb.appendChild(option(i, p.n)); });
    var tg = el('select', { id: 'f-tag' }); tg.appendChild(option('', 'タグ：すべて'));
    (DATA.vocab.tag || []).forEach(function (p, i) { tg.appendChild(option(i, p.n)); });
    var tr = el('select', { id: 'f-trait' }); tr.appendChild(option('', 'キャラ属性：すべて'));
    [cv, sf, pb, tg, tr].forEach(function (x) { row2.appendChild(x); });
    det.appendChild(row2);
    panel.appendChild(det);

    var rs = el('button', { type: 'button', id: 'f-reset', class: 'reset' });
    rs.textContent = '条件をクリア';
    panel.appendChild(rs);

    root.appendChild(panel);
    root.appendChild(el('p', { class: 'count', id: 'f-count' }));
    root.appendChild(el('ul', { class: 'cards', id: 'f-out' }));

    q.addEventListener('focus', loadSuggest, { once: true });
    q.addEventListener('input', function () { onType(); render(); });
    q.addEventListener('keydown', onKey);
    document.addEventListener('click', function (ev) {
      if (!box.contains(ev.target)) hideSug();
    });
    tr.addEventListener('focus', loadTraits, { once: true });
    [pl, se, yr, st, cv, sf, pb, tg, tr].forEach(function (x) {
      x.addEventListener('change', render);
    });
    rs.addEventListener('click', function () {
      [q, pl, se, yr, cv, sf, pb, tg, tr].forEach(function (x) { x.value = ''; });
      st.value = 'new';
      document.getElementById('m-all').checked = true;
      hideSug(); render();
    });
    render();
  }

  /* ---------------- 追加読み込み ---------------- */

  function loadTraits() {
    var sel = document.getElementById('f-trait');
    fetch(BP + '/assets/traits.json').then(function (r) { return r.json(); }).then(function (j) {
      TRAITS = j;
      sel.innerHTML = '';
      sel.appendChild(option('', 'キャラ属性：すべて'));
      j.vocab.forEach(function (t, i) { sel.appendChild(option(i, t.c + '：' + t.n)); });
    });
  }

  function loadSuggest() {
    fetch(BP + '/assets/suggest.json').then(function (r) { return r.json(); })
      .then(function (j) { SUGGEST = j; onType(); });
  }

  /* ---------------- 入力補完 ---------------- */

  var sugItems = [], sugPos = -1;

  function mode() {
    var m = document.querySelector('input[name=smode]:checked');
    return m ? m.value : 'all';
  }

  function wanted(t) {
    var m = mode();
    if (m === 'all') return true;
    if (m === 'title') return t === '作品';
    if (m === 'cv') return t === '声優' || t === 'スタッフ';
    return t === 'キャラ';
  }

  function hideSug() {
    var s = document.getElementById('f-sug');
    if (s) { s.hidden = true; s.innerHTML = ''; }
    sugItems = []; sugPos = -1;
  }

  function onType() {
    var q = norm(val('f-q'));
    var box = document.getElementById('f-sug');
    if (!SUGGEST || q.length < 1) { hideSug(); return; }
    var hits = [];
    for (var i = 0; i < SUGGEST.length && hits.length < 40; i++) {
      var s = SUGGEST[i];
      if (!wanted(s.t)) continue;
      if (norm(s.n).indexOf(q) >= 0 || (s.k && norm(s.k).indexOf(q) >= 0)) hits.push(s);
    }
    // 前方一致を上に
    hits.sort(function (a, b) {
      return (norm(a.n).indexOf(q) === 0 ? 0 : 1) - (norm(b.n).indexOf(q) === 0 ? 0 : 1);
    });
    sugItems = hits.slice(0, 12);
    sugPos = -1;
    box.innerHTML = '';
    if (!sugItems.length) { box.hidden = true; return; }
    sugItems.forEach(function (s, i) {
      var li = el('li');
      var a = el('a', { href: BP + s.u });
      a.appendChild(el('span', { class: 'k k-' + s.t, text: s.t }));
      a.appendChild(el('b', { text: s.n }));
      if (s.c) a.appendChild(el('small', { text: 'CV: ' + s.c }));
      if (s.r) a.appendChild(el('small', { text: s.r }));
      li.appendChild(a);
      li.addEventListener('mouseenter', function () { setPos(i); });
      box.appendChild(li);
    });
    box.hidden = false;
  }

  function setPos(i) {
    var box = document.getElementById('f-sug');
    Array.prototype.forEach.call(box.children, function (li, n) {
      li.className = n === i ? 'on' : '';
    });
    sugPos = i;
  }

  function onKey(ev) {
    if (!sugItems.length) return;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); setPos(Math.min(sugPos + 1, sugItems.length - 1)); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); setPos(Math.max(sugPos - 1, 0)); }
    else if (ev.key === 'Enter' && sugPos >= 0) {
      ev.preventDefault(); location.href = BP + sugItems[sugPos].u;
    } else if (ev.key === 'Escape') hideSug();
  }

  /* ---------------- 絞り込み ---------------- */

  function textMatch(it, q) {
    var m = mode();
    if (m === 'title' || m === 'all') {
      if (norm(it.t).indexOf(q) >= 0 || norm(it.l).indexOf(q) >= 0) return '作品名';
    }
    if (m === 'cv' || m === 'all') {
      for (var i = 0; i < it.c.length; i++) {
        var n = DATA.vocab.cv[it.c[i]];
        if (n && norm(n.n).indexOf(q) >= 0) return '声優: ' + n.n;
      }
      for (var j = 0; j < (it.s || []).length; j++) {
        var st = (DATA.vocab.staff || [])[it.s[j]];
        if (st && norm(st.n).indexOf(q) >= 0) return 'スタッフ: ' + st.n;
      }
    }
    if ((m === 'char' || m === 'all') && SUGGEST) {
      for (var k = 0; k < SUGGEST.length; k++) {
        var s = SUGGEST[k];
        if (s.t === 'キャラ' && s.v && norm(s.n).indexOf(q) >= 0 &&
            s.v.indexOf(it._i) >= 0) return 'キャラ: ' + s.n;
      }
    }
    return null;
  }

  function render() {
    var q = norm(val('f-q'));
    var pl = val('f-plat'), cv = val('f-cv'), sf = val('f-staff'), tg = val('f-tag'),
        tr = val('f-trait'), yr = val('f-year'), se = val('f-series'), pb = val('f-pub');

    var out = [];
    DATA.items.forEach(function (it, ix) {
      it._i = ix;
      var why = null;
      if (q) {
        why = textMatch(it, q);
        if (!why) return;
      }
      if (pl !== '' && it.p.indexOf(+pl) < 0) return;
      if (cv !== '' && it.c.indexOf(+cv) < 0) return;
      if (sf !== '' && (it.s || []).indexOf(+sf) < 0) return;
      if (se !== '' && it.e !== +se) return;
      if (pb !== '' && (it.b || []).indexOf(+pb) < 0) return;
      if (tg !== '' && it.k.indexOf(+tg) < 0) return;
      if (tr !== '' && TRAITS) {
        var x = TRAITS.items[it.v];
        if (!x || x.indexOf(+tr) < 0) return;
      }
      if (yr) {
        var y = parseInt((it.r || '').slice(0, 4), 10) || 0;
        if (yr === '2021-' && y < 2021) return;
        if (yr === '2016-2020' && (y < 2016 || y > 2020)) return;
        if (yr === '2011-2015' && (y < 2011 || y > 2015)) return;
        if (yr === '-2010' && (y > 2010 || y === 0)) return;
      }
      out.push({ it: it, why: why });
    });

    var sort = val('f-sort');
    out.sort(function (A, B) {
      var a = A.it, b = B.it;
      if (sort === 'rate') return (b.g || 0) - (a.g || 0);
      if (sort === 'pop') return (b.n || 0) - (a.n || 0);
      var x = a.r || '', y = b.r || '';
      if (sort === 'old') return x < y ? -1 : x > y ? 1 : 0;
      var fa = x > TODAY, fb = y > TODAY;
      if (fa !== fb) return fa ? 1 : -1;
      if (fa) return x < y ? -1 : x > y ? 1 : 0;
      return x > y ? -1 : x < y ? 1 : 0;
    });

    document.getElementById('f-count').textContent = out.length + '件';
    var ul = document.getElementById('f-out');
    ul.innerHTML = '';
    out.slice(0, 120).forEach(function (o) {
      var it = o.it;
      var a = el('a', { href: BP + it.u });
      if (it.i) a.appendChild(el('img', { src: it.i, alt: '', loading: 'lazy', width: '90' }));
      else a.appendChild(el('span', { class: 'noimg' }));
      var m = el('span', { class: 'meta' });
      m.appendChild(el('b', { text: it.t }));
      var soon = it.r && it.r > TODAY;
      m.appendChild(el('small', { text: (it.r || '') + (soon ? '（発売予定）' : '') }));
      m.appendChild(el('small', { text: (DATA.vocab.platform[it.p[0]] || {}).n || '' }));
      if (it.g) m.appendChild(el('small', { class: 'ex', text: '★ ' + it.g.toFixed(2) + '（' + it.n + '票）' }));
      if (o.why && o.why !== '作品名') m.appendChild(el('small', { class: 'why', text: o.why }));
      a.appendChild(m);
      ul.appendChild(el('li', {}, [a]));
    });
    if (out.length > 120) ul.appendChild(el('li', { class: 'more', text: '…ほか ' + (out.length - 120) + '件' }));
  }

  fetch(BP + '/assets/index.json').then(function (r) { return r.json(); })
    .then(function (j) { DATA = j; build(); loadSuggest(); })
    .catch(function () { root.textContent = 'データを読み込めませんでした。'; });

  // ヘッダーの検索欄から来たとき
  var p = new URLSearchParams(location.search).get('q');
  if (p) {
    var t = setInterval(function () {
      var i = document.getElementById('f-q');
      if (i) { i.value = p; render(); clearInterval(t); }
    }, 50);
  }
})();
