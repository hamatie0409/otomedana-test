/* トップページの検索と絞り込み。
   index.json だけで動き、検索欄に触れたとき suggest.json、
   キャラ属性で絞るときだけ traits.json を追加で読む。

   照合はすべて事前に正規化した文字列に対して行う。
   かな・カナ・漢字・ローマ字のどれで入力しても引けるようにしている。 */
(function () {
  var BP = window.BASE_PATH || '';
  var root = document.getElementById('app');
  if (!root) return;

  var DATA = null, TRAITS = null, SUGGEST = null;
  var CHAR_OF = null;          // 作品の添字 → その作品のキャラ（事前に作る索引）
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

  /* ---------------- 表記ゆれの吸収 ---------------- */

  // カタカナをひらがなに寄せ、空白と記号を落とす
  function norm(s) {
    if (!s) return '';
    s = s.toLowerCase().replace(/[\s　・･、。,.\-–—~〜「」『』（）()【】\[\]!！?？:：;；'"’”]/g, '');
    return s.replace(/[ァ-ヶ]/g, function (c) {
      return String.fromCharCode(c.charCodeAt(0) - 0x60);
    });
  }

  // ローマ字をひらがなに変換する。VNDBの表記はヘボン式なのでそれに合わせる
  var RO = [
    ['kkya','っきゃ'],['kkyu','っきゅ'],['kkyo','っきょ'],['sshi','っし'],['ssha','っしゃ'],
    ['sshu','っしゅ'],['ssho','っしょ'],['tchi','っち'],['ttsu','っつ'],['ccha','っちゃ'],
    ['cchu','っちゅ'],['ccho','っちょ'],
    ['kya','きゃ'],['kyu','きゅ'],['kyo','きょ'],['gya','ぎゃ'],['gyu','ぎゅ'],['gyo','ぎょ'],
    ['sha','しゃ'],['shu','しゅ'],['sho','しょ'],['sha','しゃ'],['ja','じゃ'],['ju','じゅ'],
    ['jo','じょ'],['cha','ちゃ'],['chu','ちゅ'],['cho','ちょ'],['nya','にゃ'],['nyu','にゅ'],
    ['nyo','にょ'],['hya','ひゃ'],['hyu','ひゅ'],['hyo','ひょ'],['bya','びゃ'],['byu','びゅ'],
    ['byo','びょ'],['pya','ぴゃ'],['pyu','ぴゅ'],['pyo','ぴょ'],['mya','みゃ'],['myu','みゅ'],
    ['myo','みょ'],['rya','りゃ'],['ryu','りゅ'],['ryo','りょ'],
    ['shi','し'],['chi','ち'],['tsu','つ'],['fu','ふ'],['ji','じ'],
    ['ka','か'],['ki','き'],['ku','く'],['ke','け'],['ko','こ'],
    ['sa','さ'],['su','す'],['se','せ'],['so','そ'],
    ['ta','た'],['te','て'],['to','と'],
    ['na','な'],['ni','に'],['nu','ぬ'],['ne','ね'],['no','の'],
    ['ha','は'],['hi','ひ'],['he','へ'],['ho','ほ'],
    ['ma','ま'],['mi','み'],['mu','む'],['me','め'],['mo','も'],
    ['ya','や'],['yu','ゆ'],['yo','よ'],
    ['ra','ら'],['ri','り'],['ru','る'],['re','れ'],['ro','ろ'],
    ['wa','わ'],['wo','を'],
    ['ga','が'],['gi','ぎ'],['gu','ぐ'],['ge','げ'],['go','ご'],
    ['za','ざ'],['zu','ず'],['ze','ぜ'],['zo','ぞ'],
    ['da','だ'],['de','で'],['do','ど'],['di','ぢ'],['du','づ'],
    ['ba','ば'],['bi','び'],['bu','ぶ'],['be','べ'],['bo','ぼ'],
    ['pa','ぱ'],['pi','ぴ'],['pu','ぷ'],['pe','ぺ'],['po','ぽ'],
    ['a','あ'],['i','い'],['u','う'],['e','え'],['o','お'],['n','ん']
  ];

  function romajiToKana(s) {
    if (!s) return '';
    s = s.toLowerCase().replace(/[^a-z]/g, '');
    var out = '', i = 0;
    outer: while (i < s.length) {
      // 促音（同じ子音が続く）
      if (i + 1 < s.length && s[i] === s[i + 1] && 'kstpgzdbjcf'.indexOf(s[i]) >= 0) {
        out += 'っ'; i++; continue;
      }
      for (var r = 0; r < RO.length; r++) {
        var k = RO[r][0];
        if (s.substr(i, k.length) === k) { out += RO[r][1]; i += k.length; continue outer; }
      }
      i++;   // 変換できない文字は捨てる
    }
    return out;
  }

  /* ---------------- 組み立て ---------------- */

  function build() {
    var panel = el('div', { class: 'search-panel' });
    panel.appendChild(el('h2', { text: '乙女ゲームを探す', class: 'sp-title' }));

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

    var box = el('div', { class: 'sp-box' });
    var q = el('input', { type: 'search', id: 'f-q', autocomplete: 'off',
                          placeholder: '作品名・声優名・キャラクター名（かな・ローマ字も可）' });
    var sug = el('ul', { class: 'sp-sug', id: 'f-sug', hidden: 'hidden' });
    box.appendChild(q);
    box.appendChild(sug);
    panel.appendChild(box);

    var row = el('div', { class: 'filters' });
    var pl = el('select', { id: 'f-plat' });
    pl.appendChild(option('', '機種：すべて'));
    var groups = {};
    (DATA.vocab.platform || []).forEach(function (p, i) {
      var g = p.g || 'その他';
      if (!groups[g]) { groups[g] = el('optgroup', { label: g }); pl.appendChild(groups[g]); }
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

    // 打鍵のたびに全件を走らせない
    var timer = null;
    q.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () { onType(); render(); }, 120);
    });
    q.addEventListener('keydown', onKey);
    document.addEventListener('click', function (ev) { if (!box.contains(ev.target)) hideSug(); });
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

  /* ---------------- 追加読み込みと索引づくり ---------------- */

  function loadTraits() {
    var sel = document.getElementById('f-trait');
    fetch(BP + '/assets/traits.json').then(function (r) { return r.json(); }).then(function (j) {
      TRAITS = j;
      sel.innerHTML = '';
      sel.appendChild(option('', 'キャラ属性：すべて'));
      j.vocab.forEach(function (t, i) { sel.appendChild(option(i, t.c + '：' + t.n)); });
    });
  }

  function prepare() {
    // 照合用の文字列を1度だけ作る
    DATA.items.forEach(function (it, i) {
      it._i = i;
      it._n = norm(it.t);
      it._l = norm(it.l);
      it._r = romajiToKana(it.l);      // 作品名をかなでも引けるように
    });
    (DATA.vocab.cv || []).forEach(function (v) { v._n = norm(v.n); });
    (DATA.vocab.staff || []).forEach(function (v) { v._n = norm(v.n); });

    SUGGEST.forEach(function (s) {
      s._n = norm(s.n);
      s._k = norm(s.k);
      s._r = romajiToKana(s.k);     // ローマ字から起こしたかな
    });

    // 声優・スタッフにも読みを渡す（一覧の絞り込みでもかな・ローマ字を効かせる）
    var stByName = {};
    (DATA.vocab.staff || []).forEach(function (v) { stByName[v._n] = v; });
    SUGGEST.forEach(function (s) {
      if (s.t === '声優' && typeof s.i === 'number' && DATA.vocab.cv[s.i]) {
        DATA.vocab.cv[s.i]._k = s._k;
        DATA.vocab.cv[s.i]._r = s._r;
      } else if (s.t === 'スタッフ') {
        var v = stByName[s._n];
        if (v) { v._k = s._k; v._r = s._r; }
      }
    });

    // 作品 → その作品のキャラ（毎回 SUGGEST を走査しないための索引）
    CHAR_OF = new Array(DATA.items.length);
    SUGGEST.forEach(function (s) {
      if (s.t !== 'キャラ' || !s.v) return;
      for (var i = 0; i < s.v.length; i++) {
        var ix = s.v[i];
        (CHAR_OF[ix] || (CHAR_OF[ix] = [])).push(s);
      }
    });
  }

  function loadSuggest() {
    return fetch(BP + '/assets/suggest.json').then(function (r) { return r.json(); })
      .then(function (j) { SUGGEST = j; prepare(); });
  }

  /* ---------------- 入力補完 ---------------- */

  var sugItems = [], sugPos = -1;

  function mode() {
    var m = document.querySelector('input[name=smode]:checked');
    return m ? m.value : 'all';
  }
  function wanted(t, m) {
    if (m === 'all') return true;
    if (m === 'title') return t === '作品';
    if (m === 'cv') return t === '声優' || t === 'スタッフ';
    return t === 'キャラ';
  }
  function hit(s, q) {
    return s._n.indexOf(q) >= 0 || (s._k && s._k.indexOf(q) >= 0) ||
           (s._r && s._r.indexOf(q) >= 0);
  }
  function hideSug() {
    var s = document.getElementById('f-sug');
    if (s) { s.hidden = true; s.innerHTML = ''; }
    sugItems = []; sugPos = -1;
  }

  function onType() {
    var q = norm(val('f-q'));
    var box = document.getElementById('f-sug');
    if (!SUGGEST || !q) { hideSug(); return; }
    var m = mode();                 // DOM参照は1回だけ
    var head = [], rest = [];
    for (var i = 0; i < SUGGEST.length; i++) {
      var s = SUGGEST[i];
      if (!wanted(s.t, m) || !hit(s, q)) continue;
      (s._n.indexOf(q) === 0 || (s._r && s._r.indexOf(q) === 0) ? head : rest).push(s);
      if (head.length >= 12 || rest.length >= 200) break;
    }
    sugItems = head.concat(rest).slice(0, 12);
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
    Array.prototype.forEach.call(box.children, function (li, n) { li.className = n === i ? 'on' : ''; });
    sugPos = i;
  }
  function onKey(ev) {
    if (!sugItems.length) return;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); setPos(Math.min(sugPos + 1, sugItems.length - 1)); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); setPos(Math.max(sugPos - 1, 0)); }
    else if (ev.key === 'Enter' && sugPos >= 0) { ev.preventDefault(); location.href = BP + sugItems[sugPos].u; }
    else if (ev.key === 'Escape') hideSug();
  }

  /* ---------------- 絞り込み ---------------- */

  function textMatch(it, q, m) {
    var i;
    if (m === 'title' || m === 'all') {
      if (it._n.indexOf(q) >= 0 || it._l.indexOf(q) >= 0 ||
          (it._r && it._r.indexOf(q) >= 0)) return '作品名';
    }
    if (m === 'cv' || m === 'all') {
      for (i = 0; i < it.c.length; i++) {
        var n = DATA.vocab.cv[it.c[i]];
        if (n && hit(n, q)) return '声優: ' + n.n;
      }
      for (i = 0; i < (it.s || []).length; i++) {
        var st = (DATA.vocab.staff || [])[it.s[i]];
        if (st && hit(st, q)) return 'スタッフ: ' + st.n;
      }
    }
    if ((m === 'char' || m === 'all') && CHAR_OF) {
      var cs = CHAR_OF[it._i];
      if (cs) for (i = 0; i < cs.length; i++) {
        if (hit(cs[i], q)) return 'キャラ: ' + cs[i].n;
      }
    }
    return null;
  }

  function render() {
    var q = norm(val('f-q'));
    var pl = val('f-plat'), cv = val('f-cv'), sf = val('f-staff'), tg = val('f-tag'),
        tr = val('f-trait'), yr = val('f-year'), se = val('f-series'), pb = val('f-pub');

    var m = mode();                 // DOM参照は1回だけ
    var out = [];
    for (var n = 0; n < DATA.items.length; n++) {
      var it = DATA.items[n], why = null;
      if (q) { why = textMatch(it, q, m); if (!why) continue; }
      if (pl !== '' && it.p.indexOf(+pl) < 0) continue;
      if (cv !== '' && it.c.indexOf(+cv) < 0) continue;
      if (sf !== '' && (it.s || []).indexOf(+sf) < 0) continue;
      if (se !== '' && it.e !== +se) continue;
      if (pb !== '' && (it.b || []).indexOf(+pb) < 0) continue;
      if (tg !== '' && it.k.indexOf(+tg) < 0) continue;
      if (tr !== '' && TRAITS) {
        var x = TRAITS.items[it.v];
        if (!x || x.indexOf(+tr) < 0) continue;
      }
      if (yr) {
        var y = parseInt((it.r || '').slice(0, 4), 10) || 0;
        if (yr === '2021-' && y < 2021) continue;
        if (yr === '2016-2020' && (y < 2016 || y > 2020)) continue;
        if (yr === '2011-2015' && (y < 2011 || y > 2015)) continue;
        if (yr === '-2010' && (y > 2010 || y === 0)) continue;
      }
      out.push({ it: it, why: why });
    }

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
    var frag = document.createDocumentFragment();
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
      frag.appendChild(el('li', {}, [a]));
    });
    if (out.length > 120) frag.appendChild(el('li', { class: 'more', text: '…ほか ' + (out.length - 120) + '件' }));
    ul.innerHTML = '';
    ul.appendChild(frag);
  }

  fetch(BP + '/assets/index.json').then(function (r) { return r.json(); })
    .then(function (j) {
      DATA = j;
      SUGGEST = [];
      prepare();
      build();
      return loadSuggest();
    })
    .then(function () {
      var i = document.getElementById('f-q');
      if (i && i.value) { onType(); render(); }
    })
    .catch(function () { root.textContent = 'データを読み込めませんでした。'; });

  var p = new URLSearchParams(location.search).get('q');
  if (p) {
    var t = setInterval(function () {
      var i = document.getElementById('f-q');
      if (i) { i.value = p; render(); clearInterval(t); }
    }, 50);
  }
})();
