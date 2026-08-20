/* トップページの絞り込み。index.json だけで動き、
   キャラ属性で絞るときにだけ traits.json を追加取得する。 */
(function () {
  var BP = window.BASE_PATH || '';
  var root = document.getElementById('app');
  if (!root) return;

  var DATA = null, TRAITS = null;

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

  function build() {
    var f = el('div', { class: 'filters' });

    var q = el('input', { type: 'search', placeholder: '作品名で絞り込む', id: 'f-q' });
    var pl = el('select', { id: 'f-plat' }); pl.appendChild(option('', '機種'));
    DATA.vocab.platform.forEach(function (p, i) { pl.appendChild(option(i, p.n)); });
    var cv = el('select', { id: 'f-cv' }); cv.appendChild(option('', '声優'));
    DATA.vocab.cv.forEach(function (p, i) { cv.appendChild(option(i, p.n)); });
    var sf = el('select', { id: 'f-staff' }); sf.appendChild(option('', 'スタッフ'));
    (DATA.vocab.staff || []).forEach(function (p, i) {
      sf.appendChild(option(i, p.n + (p.r ? '（' + p.r + '）' : '')));
    });
    var se = el('select', { id: 'f-series' }); se.appendChild(option('', 'シリーズ'));
    (DATA.vocab.series || []).forEach(function (p, i) {
      se.appendChild(option(i, p.n + '（' + p.c + '作品）'));
    });
    var pb = el('select', { id: 'f-pub' }); pb.appendChild(option('', '発売元'));
    (DATA.vocab.publisher || []).forEach(function (p, i) { pb.appendChild(option(i, p.n)); });
    var tg = el('select', { id: 'f-tag' }); tg.appendChild(option('', 'タグ'));
    DATA.vocab.tag.forEach(function (p, i) { tg.appendChild(option(i, p.n)); });
    var tr = el('select', { id: 'f-trait' }); tr.appendChild(option('', 'キャラ属性'));
    var yr = el('select', { id: 'f-year' }); yr.appendChild(option('', '発売年'));
    ['2021-', '2016-2020', '2011-2015', '-2010'].forEach(function (y) { yr.appendChild(option(y, y)); });
    var st = el('select', { id: 'f-sort' });
    st.appendChild(option('new', '発売日が新しい順'));
    st.appendChild(option('old', '発売日が古い順'));
    st.appendChild(option('rate', '評価が高い順'));
    st.appendChild(option('pop', '票数が多い順'));
    var rs = el('button', { type: 'button', id: 'f-reset', class: 'reset' });
    rs.textContent = '条件をクリア';

    [q, pl, se, cv, sf, pb, tg, tr, yr, st, rs].forEach(function (x) { f.appendChild(x); });
    root.appendChild(f);
    root.appendChild(el('p', { class: 'count', id: 'f-count' }));
    root.appendChild(el('ul', { class: 'cards', id: 'f-out' }));

    tr.addEventListener('focus', loadTraits, { once: true });
    [q, pl, se, cv, sf, pb, tg, tr, yr, st].forEach(function (x) {
      x.addEventListener('input', render); x.addEventListener('change', render);
    });
    rs.addEventListener('click', function () {
      [q, pl, se, cv, sf, pb, tg, tr, yr].forEach(function (x) { x.value = ''; });
      st.value = 'new'; render();
    });
    render();
  }

  function loadTraits() {
    var sel = document.getElementById('f-trait');
    sel.appendChild(option('', '読み込み中…'));
    fetch(BP + '/assets/traits.json').then(function (r) { return r.json(); }).then(function (j) {
      TRAITS = j;
      sel.innerHTML = '';
      sel.appendChild(option('', 'キャラ属性'));
      j.vocab.forEach(function (t, i) { sel.appendChild(option(i, t.c + '：' + t.n)); });
    });
  }

  function render() {
    var v = function (id) { return document.getElementById(id).value; };
    var q = v('f-q').toLowerCase(), pl = v('f-plat'), cv = v('f-cv'),
        sf = v('f-staff'), tg = v('f-tag'), tr = v('f-trait'), yr = v('f-year'),
        se = v('f-series'), pb = v('f-pub');
    var out = DATA.items.filter(function (it) {
      if (q) {
        var hit = (it.t || '').toLowerCase().indexOf(q) >= 0 ||
                  (it.l || '').toLowerCase().indexOf(q) >= 0;
        if (!hit) {                        // 声優名でも引けるようにする
          hit = it.c.some(function (i) {
            var n = DATA.vocab.cv[i]; return n && n.n.toLowerCase().indexOf(q) >= 0;
          });
        }
        if (!hit) {                        // スタッフ名でも引けるようにする
          hit = (it.s || []).some(function (i) {
            var n = (DATA.vocab.staff || [])[i];
            return n && n.n.toLowerCase().indexOf(q) >= 0;
          });
        }
        if (!hit) return false;
      }
      if (pl !== '' && it.p.indexOf(+pl) < 0) return false;
      if (cv !== '' && it.c.indexOf(+cv) < 0) return false;
      if (sf !== '' && (it.s || []).indexOf(+sf) < 0) return false;
      if (se !== '' && it.e !== +se) return false;
      if (pb !== '' && (it.b || []).indexOf(+pb) < 0) return false;
      if (tg !== '' && it.k.indexOf(+tg) < 0) return false;
      if (tr !== '' && TRAITS) {
        var x = TRAITS.items[it.v];
        if (!x || x.indexOf(+tr) < 0) return false;
      }
      if (yr) {
        var y = parseInt((it.r || '').slice(0, 4), 10) || 0;
        if (yr === '2021-' && y < 2021) return false;
        if (yr === '2016-2020' && (y < 2016 || y > 2020)) return false;
        if (yr === '2011-2015' && (y < 2011 || y > 2015)) return false;
        if (yr === '-2010' && (y > 2010 || y === 0)) return false;
      }
      return true;
    });

    var sort = document.getElementById('f-sort').value;
    var today = new Date().toISOString().slice(0, 10);
    out.sort(function (a, b) {
      if (sort === 'rate') return (b.g || 0) - (a.g || 0);
      if (sort === 'pop') return (b.n || 0) - (a.n || 0);
      var x = a.r || '', y = b.r || '';
      if (sort === 'old') return x < y ? -1 : x > y ? 1 : 0;
      // 発売日が新しい順：発売済みを先に、発売予定は末尾へ回す
      var fa = x > today, fb = y > today;
      if (fa !== fb) return fa ? 1 : -1;
      if (fa) return x < y ? -1 : x > y ? 1 : 0;   // 予定は近い順
      return x > y ? -1 : x < y ? 1 : 0;
    });
    document.getElementById('f-count').textContent = out.length + '件';
    var ul = document.getElementById('f-out');
    ul.innerHTML = '';
    out.slice(0, 120).forEach(function (it) {
      var a = el('a', { href: BP + it.u });
      if (it.i) a.appendChild(el('img', { src: it.i, alt: '', loading: 'lazy', width: '90' }));
      else a.appendChild(el('span', { class: 'noimg' }));
      var m = el('span', { class: 'meta' });
      m.appendChild(el('b', { text: it.t }));
      var soon = it.r && it.r > today;
      m.appendChild(el('small', { text: (it.r || '') + (soon ? '（発売予定）' : '') }));
      m.appendChild(el('small', { text: (DATA.vocab.platform[it.p[0]] || {}).n || '' }));
      if (it.g) m.appendChild(el('small', { class: 'ex', text: '★ ' + it.g.toFixed(2) + '（' + it.n + '票）' }));
      a.appendChild(m);
      ul.appendChild(el('li', {}, [a]));
    });
    if (out.length > 120) {
      ul.appendChild(el('li', { text: '…ほか ' + (out.length - 120) + '件' }));
    }
  }

  fetch(BP + '/assets/index.json').then(function (r) { return r.json(); })
    .then(function (j) { DATA = j; build(); })
    .catch(function () { root.textContent = 'データを読み込めませんでした。'; });

  // ヘッダー検索から来たとき
  var p = new URLSearchParams(location.search).get('q');
  if (p) { var t = setInterval(function () {
    var i = document.getElementById('f-q');
    if (i) { i.value = p; i.dispatchEvent(new Event('input')); clearInterval(t); }
  }, 50); }
})();
