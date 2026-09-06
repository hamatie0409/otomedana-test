/* ============================================================================
   オトメ棚 v2 — ブラウザ側の動き

   受け持つのは3つだけ。
     1. 検索と絞り込み（トップ・一覧ページ）
     2. 購入導線の「機種 → 版 → 販売店」の切り替え
     3. MY棚（所持・欲しい・進捗・メモ）と、そこから出す好みの傾向

   MY棚のデータはこのブラウザの localStorage にだけ置く。
   サーバーへ送らない。GitHub Pages は静的配信なので置き場所が無いのと、
   ログインを作らずに使えるほうが導入の敷居が低いため。

   JSが無くても、作品ページ・一覧・購入先リンクはHTMLだけで読める。
   ここで足しているのは絞り込みと記録だけ。
   ========================================================================= */
(function () {
  'use strict';

  var BASE = window.V2_BASE || '';
  var KEY = 'otomedana.shelf.v1';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var yen = function (n) { return '¥' + Number(n).toLocaleString('ja-JP'); };

  var STATUS = {
    want:     { label: '♡ 欲しい',  badge: 'tag-outline', owned: false },
    reserved: { label: '予約済',    badge: 'tag-outline', owned: false },
    owned:    { label: '未プレイ',  badge: 'tag-neutral', owned: true },
    playing:  { label: '▶ プレイ中', badge: 'tag-accent',  owned: true },
    cleared:  { label: '✓ クリア',  badge: 'tag-neutral', owned: true }
  };

  /* ---------------------------------------------------------------- 保存 */
  var Shelf = {
    data: {},
    load: function () {
      try { this.data = JSON.parse(localStorage.getItem(KEY)) || {}; }
      catch (err) { this.data = {}; }
      return this.data;
    },
    save: function () {
      try { localStorage.setItem(KEY, JSON.stringify(this.data)); }
      catch (err) {
        alert('保存できませんでした。ブラウザの設定で保存が禁止されている可能性があります。');
      }
      document.dispatchEvent(new CustomEvent('shelf:change'));
    },
    get: function (vid) { return this.data[vid]; },
    set: function (vid, patch) {
      var cur = this.data[vid] || { at: Date.now() };
      for (var k in patch) if (patch.hasOwnProperty(k)) cur[k] = patch[k];
      this.data[vid] = cur;
      this.save();
    },
    remove: function (vid) { delete this.data[vid]; this.save(); },
    /* 所持＝欲しい・予約済 以外。デザインの集計もこの数え方 */
    ownedVids: function () {
      var out = [];
      for (var v in this.data) {
        if (this.data.hasOwnProperty(v) && STATUS[this.data[v].s] && STATUS[this.data[v].s].owned) out.push(v);
      }
      return out;
    },
    count: function (kind) {
      var n = 0;
      for (var v in this.data) {
        if (!this.data.hasOwnProperty(v)) continue;
        var s = this.data[v].s;
        if (kind === 'owned') { if (STATUS[s] && STATUS[s].owned) n++; }
        else if (kind === 'backlog') { if (s === 'owned') n++; }
        else if (s === kind) n++;
      }
      return n;
    }
  };
  Shelf.load();

  /* ---------------------------------------------------------------- 読み込み */
  var cache = {};
  function fetchJSON(name) {
    if (cache[name]) return cache[name];
    cache[name] = fetch(BASE + '/assets/' + name)
      .then(function (r) { if (!r.ok) throw new Error(name); return r.json(); })
      .catch(function () { return null; });
    return cache[name];
  }

  /* かな・ローマ字も拾えるよう、カタカナ→ひらがな＋小文字化で正規化する */
  function norm(s) {
    return (s || '').toString().toLowerCase()
      .replace(/[ァ-ヶ]/g, function (c) {
        return String.fromCharCode(c.charCodeAt(0) - 0x60);
      })
      .replace(/[！-～]/g, function (c) {
        return String.fromCharCode(c.charCodeAt(0) - 0xfee0);
      })
      .replace(/[\s　・:：\-—―ー~〜!！?？'"'']/g, '');
  }

  /* ---------------------------------------------------------------- 共通表示 */
  function paintHeaderCount() {
    var el = $('[data-shelf-count]');
    if (!el) return;
    var o = Shelf.count('owned'), w = Shelf.count('want');
    if (!o && !w) { el.hidden = true; return; }
    el.hidden = false;
    el.textContent = '所持 ' + o + '本 / 欲しい ' + w + '本';
  }

  /* 一覧に並ぶカードへ所持マークを入れる */
  function paintCards(root) {
    $$('.wcard[data-vid]', root || document).forEach(function (a) {
      var slot = $('[data-own]', a);
      if (!slot) return;
      var it = Shelf.get(a.getAttribute('data-vid'));
      slot.textContent = it && STATUS[it.s] ? STATUS[it.s].label : '';
    });
  }

  /* ---------------------------------------------------------------- 一覧の絞り込み */
  function initListFilter() {
    var input = $('[data-list-filter]');
    var grid = $('[data-list-grid]');
    if (!input || !grid) return;
    var empty = $('[data-list-empty]');
    var items = Array.prototype.slice.call(grid.children).map(function (el) {
      return { el: el, key: norm(el.textContent) };
    });
    input.addEventListener('input', function () {
      var q = norm(input.value);
      var hit = 0;
      items.forEach(function (it) {
        var ok = !q || it.key.indexOf(q) >= 0;
        it.el.hidden = !ok;
        if (ok) hit++;
      });
      if (empty) empty.hidden = hit > 0;
    });
  }

  /* ---------------------------------------------------------------- トップの検索 */
  function initHomeSearch() {
    var panel = $('#search');
    if (!panel) return;
    var results = $('[data-results]');
    var grid = $('[data-results-grid]');
    var countEl = $('[data-results-count]');
    var browse = $('[data-browse]');
    var more = $('[data-more-results]');
    var q = $('#q');
    var scope = 'all';
    var shown = 0, hits = [];
    var PAGE = 24;

    $$('.scope').forEach(function (b) {
      b.addEventListener('click', function () {
        scope = b.getAttribute('data-scope');
        $$('.scope').forEach(function (x) {
          x.setAttribute('aria-pressed', String(x === b));
        });
        q.placeholder = {
          all: '作品名・声優名・キャラクター名（かな・ローマ字も可）',
          title: '作品名（かな・ローマ字も可）',
          cv: '声優名（かな・ローマ字も可）',
          char: 'キャラクター名（かな・ローマ字も可）'
        }[scope];
        run();
      });
    });

    var toggle = $('[data-filter-toggle]');
    var filters = $('[data-filters]');
    if (toggle && filters) {
      toggle.addEventListener('click', function () {
        var open = toggle.getAttribute('aria-expanded') === 'true';
        toggle.setAttribute('aria-expanded', String(!open));
        filters.hidden = open;
        toggle.textContent = open ? '絞り込み（機種・年・年齢・並び順）＋' : '絞り込みを閉じる −';
      });
      if (window.matchMedia('(max-width:700px)').matches) filters.hidden = true;
    }

    function val(name) {
      var el = $('[data-f="' + name + '"]');
      return el ? el.value : '';
    }

    function render(reset) {
      if (reset) { shown = 0; grid.innerHTML = ''; }
      var slice = hits.slice(shown, shown + PAGE);
      var frag = document.createDocumentFragment();
      slice.forEach(function (w) { frag.appendChild(card(w)); });
      grid.appendChild(frag);
      shown += slice.length;
      if (more) more.hidden = shown >= hits.length;
      countEl.textContent = hits.length + '件中 ' + shown + '件を表示';
      paintCards(grid);
    }

    function card(w) {
      var a = document.createElement('a');
      a.className = 'wcard';
      a.href = BASE + w.u;
      a.setAttribute('data-vid', w.v);
      a.innerHTML =
        (w.c ? '<img class="thumb cover" src="' + w.c + '" alt="" loading="lazy">'
             : '<div class="thumb ph"><span>画像なし</span></div>') +
        '<div class="body"><div class="t"></div><div class="m"></div>' +
        '<div class="r"><span class="score"></span><span class="own" data-own></span></div></div>';
      $('.t', a).textContent = w.t;
      $('.m', a).textContent = [w.dj, w.p].filter(Boolean).join(' ');
      $('.score', a).textContent = w.rt;
      return a;
    }

    function run() {
      Promise.all([fetchJSON('index.json'), fetchJSON('suggest.json')]).then(function (r) {
        var idx = r[0], sug = r[1];
        if (!idx) return;
        var term = norm(q.value);
        var plat = val('plat'), year = val('year'), age = val('age'), sort = val('sort') || 'date_desc';
        var active = term || plat || year || age;
        if (!active) {
          results.hidden = true;
          if (browse) browse.hidden = false;
          return;
        }
        var allow = null;
        if (term && sug && (scope === 'cv' || scope === 'char' || scope === 'all')) {
          allow = {};
          var add = function (vs) { vs.forEach(function (v) { allow[v] = 1; }); };
          if (scope !== 'char') {
            Object.keys(sug.cv).forEach(function (name) {
              if (norm(name).indexOf(term) >= 0) add(sug.cv[name].w);
            });
          }
          if (scope !== 'cv') {
            Object.keys(sug.char).forEach(function (name) {
              if (norm(name).indexOf(term) >= 0) add(sug.char[name]);
            });
          }
        }
        hits = idx.filter(function (w) {
          if (plat && (w.ps || '').indexOf(plat) < 0) return false;
          if (year && String(w.y) !== year) return false;
          if (age && w.a !== age) return false;
          if (!term) return true;
          var byTitle = norm(w.t).indexOf(term) >= 0 || (w.l || '').indexOf(term) >= 0;
          if (scope === 'title') return byTitle;
          if (scope === 'cv' || scope === 'char') return allow && allow[w.v];
          return byTitle || (allow && allow[w.v]);
        });
        hits.sort(function (a, b) {
          if (sort === 'date_asc') return (a.d || '9999').localeCompare(b.d || '9999');
          if (sort === 'rating') return (b.r || 0) - (a.r || 0);
          if (sort === 'votes') return (b.n || 0) - (a.n || 0);
          if (sort === 'title') return a.t.localeCompare(b.t, 'ja');
          return (b.d || '').localeCompare(a.d || '');
        });
        results.hidden = false;
        if (browse) browse.hidden = true;
        render(true);
      });
    }

    var timer;
    q.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(run, 180);
    });
    $$('[data-f]').forEach(function (s) { s.addEventListener('change', run); });
    var go = $('[data-search]');
    if (go) go.addEventListener('click', run);
    if (more) more.addEventListener('click', function () { render(false); });
    var clear = $('[data-clear]');
    if (clear) clear.addEventListener('click', function () {
      q.value = '';
      $$('[data-f]').forEach(function (s) { s.selectedIndex = 0; });
      run();
    });
    if (location.hash === '#search') q.focus();
  }

  /* ---------------------------------------------------------------- 購入導線 */
  function initBuy() {
    var tabs = $('[data-plat-tabs]');
    var eds = $('[data-eds]');
    var table = $('[data-shops]');
    if (!tabs || !eds || !table) return;
    var nameEl = $('[data-ed-name]');
    var plat = null, eid = null;

    function edsOf(p) {
      return $$('.ed', eds).filter(function (d) { return d.getAttribute('data-plat') === p; });
    }
    function paint() {
      $$('button', tabs).forEach(function (b) {
        b.setAttribute('aria-pressed', String(b.getAttribute('data-plat') === plat));
      });
      $$('.ed', eds).forEach(function (d) {
        d.hidden = d.getAttribute('data-plat') !== plat;
        var on = d.getAttribute('data-eid') === eid;
        d.setAttribute('data-sel', on ? '1' : '0');
        var btn = $('[data-pick-ed]', d);
        if (btn) {
          btn.className = 'btn btn-block ' + (on ? 'btn-primary' : 'btn-secondary');
          btn.textContent = on ? '選択中' : '販売店を見る';
        }
      });
      $$('tbody tr', table).forEach(function (tr) {
        tr.hidden = tr.getAttribute('data-eid') !== eid;
      });
      var cur = $('.ed[data-eid="' + eid + '"]', eds);
      var tab = $('[data-plat="' + plat + '"]', tabs);
      if (nameEl && cur) {
        nameEl.textContent = [tab ? tab.textContent : '', $('.ed-name', cur).textContent]
          .filter(Boolean).join(' ');
      }
      markOwnedEditions();
    }
    function pickPlat(p) {
      plat = p;
      var list = edsOf(p);
      eid = list.length ? list[0].getAttribute('data-eid') : null;
      paint();
    }
    $$('button', tabs).forEach(function (b) {
      b.addEventListener('click', function () { pickPlat(b.getAttribute('data-plat')); });
    });
    $$('[data-pick-ed]', eds).forEach(function (b) {
      b.addEventListener('click', function () {
        eid = b.getAttribute('data-pick-ed');
        paint();
      });
    });
    var first = $('button', tabs);
    if (first) pickPlat(first.getAttribute('data-plat'));
  }

  /* 所持している版に「✓ 所持」を出して二重購入を防ぐ */
  function markOwnedEditions() {
    var panel = $('[data-shelf-panel]');
    if (!panel) return;
    var it = Shelf.get(panel.getAttribute('data-vid'));
    $$('[data-own-ed]').forEach(function (s) {
      var ed = s.closest('.ed');
      var on = !!(it && STATUS[it.s] && STATUS[it.s].owned && it.e &&
                  ed && $('.ed-name', ed).textContent === it.e);
      s.hidden = !on;
    });
  }

  /* ---------------------------------------------------------------- 作品ページのMY棚 */
  function initShelfPanel() {
    var panel = $('[data-shelf-panel]');
    if (!panel) return;
    var vid = panel.getAttribute('data-vid');
    var title = panel.getAttribute('data-title');

    function paint() {
      var it = Shelf.get(vid);
      $$('button', $('[data-shelf-status]', panel)).forEach(function (b) {
        b.setAttribute('aria-pressed', String(!!it && it.s === b.getAttribute('data-status')));
      });
      var own = $('[data-shelf-own]', panel);
      var state = $('[data-shelf-state]', panel);
      var bar = $('[data-shelf-bar]', panel);
      if (!it) {
        own.hidden = true;
        state.textContent = '未登録';
        bar.style.width = '0%';
        return;
      }
      own.hidden = !(STATUS[it.s] && STATUS[it.s].owned);
      if (!own.hidden) {
        var what = [it.p, it.e].filter(Boolean).join(' ');
        $('b', own).textContent = what ? '✓ ' + what + 'を所持' : '✓ 所持しています';
        $('small', own).textContent =
          [it.d, it.sh, it.pr ? yen(it.pr) : ''].filter(Boolean).join('・') || '購入の記録なし';
      }
      var read = Number(it.rd || 0), total = Number(it.tt || 0);
      state.textContent = '現在：' + (STATUS[it.s] ? STATUS[it.s].label.replace(/^[^ ]* /, '') : it.s) +
        (total ? '・' + read + '/' + total + 'ルート読了' : '');
      bar.style.width = (total ? Math.round(read / total * 100) : (it.s === 'cleared' ? 100 : 0)) + '%';
    }

    $$('button', $('[data-shelf-status]', panel)).forEach(function (b) {
      b.addEventListener('click', function () {
        var s = b.getAttribute('data-status');
        var cur = Shelf.get(vid);
        if (cur && cur.s === s) Shelf.remove(vid);
        else Shelf.set(vid, { s: s, t: title });
      });
    });
    var edit = $('[data-shelf-edit]', panel);
    if (edit) edit.addEventListener('click', function () {
      location.href = BASE + '/my/?edit=' + encodeURIComponent(vid);
    });
    document.addEventListener('shelf:change', function () { paint(); markOwnedEditions(); });
    paint();
  }

  /* ---------------------------------------------------------------- 好みとの一致 */
  function pctBar(w) { return '<span class="bar"><i style="width:' + w + '%"></i></span>'; }

  /* 棚の作品に多く出てくる属性ほど「好み」とみなす。
     ただし出現数をそのまま使うと、どの作品にもある属性が必ず上位に来る。
     珍しい属性ほど効くよう log(全作品数 / その属性を持つ作品数) を掛ける（IDF）。 */
  function tasteProfile(tr) {
    var owned = Shelf.ownedVids();
    var count = {};
    owned.forEach(function (v) {
      (tr.works[v] || []).forEach(function (i) { count[i] = (count[i] || 0) + 1; });
    });
    var N = tr.n || 1;
    var rank = Object.keys(count).map(function (i) {
      i = Number(i);
      var d = (tr.df && tr.df[i]) || 1;
      return { i: i, n: count[i], w: count[i] * Math.log(N / d) };
    }).sort(function (a, b) { return b.w - a.w || b.n - a.n; });
    return { owned: owned, rank: rank, count: count };
  }

  function initTaste() {
    var sec = $('[data-taste]');
    if (!sec) return;
    var vid = sec.getAttribute('data-vid');
    function paint() {
      Promise.all([fetchJSON('traits.json'), fetchJSON('index.json')]).then(function (r) {
        var tr = r[0], idx = r[1];
        if (!tr || !idx) return;
        var p = tasteProfile(tr);
        var lead = $('[data-taste-lead]', sec);
        var body = $('[data-taste-body]', sec);
        var recEl = $('[data-taste-rec]', sec);
        if (!p.owned.length) {
          lead.textContent = 'MY棚に作品を登録すると、あなたがよく選んでいる属性とこの作品を比べます。';
          body.innerHTML = '';
          recEl.innerHTML = '<p class="empty">MY棚が空です。</p>';
          return;
        }
        var mine = tr.works[vid] || [];
        var mineSet = {};
        mine.forEach(function (i) { mineSet[i] = 1; });
        var top = p.rank.slice(0, 5);
        var hit = top.filter(function (t) { return mineSet[t.i]; }).length;
        lead.textContent = '所持' + p.owned.length + '本から抽出した属性と、この作品の属性を比べています。';
        var max = top.length ? top[0].n : 1;
        body.innerHTML =
          '<div class="match-big"><b>' + (top.length ? Math.round(hit / top.length * 100) : 0) + '%</b>' +
          '<span>一致（あなたの上位属性 ' + top.length + '件中 ' + hit + '件）</span></div>' +
          top.map(function (t) {
            return '<div class="trow"><span class="nm">' + tr.names[t.i] + '</span>' +
              pctBar(Math.round(t.n / max * 100)) +
              '<span class="ct">' + t.n + '作品' + (mineSet[t.i] ? '・この作品にあり' : '') + '</span></div>';
          }).join('');
        recEl.innerHTML = recommendHTML(tr, idx, p, 3);
      });
    }
    document.addEventListener('shelf:change', paint);
    paint();
  }

  /* 未所持の作品を、上位属性との重なりが多い順に薦める */
  function recommendHTML(tr, idx, prof, n) {
    var top = prof.rank.slice(0, 12);
    if (!top.length) return '<p class="empty">MY棚が空です。</p>';
    var weight = {};
    top.forEach(function (t) { weight[t.i] = t.w; });
    var full = top.reduce(function (a, t) { return a + t.w; }, 0) || 1;
    var scored = idx.filter(function (w) { return !Shelf.get(w.v); })
      .map(function (w) {
        var s = 0, ts = tr.works[w.v] || [];
        ts.forEach(function (i) { if (weight[i]) s += weight[i]; });
        return { w: w, s: s };
      })
      .filter(function (x) { return x.s > 0; })
      .sort(function (a, b) { return b.s - a.s || (b.w.r || 0) - (a.w.r || 0); });
    /* 同じシリーズは登場人物が同じで属性も同じになるため、上位が続編で埋まる。
       1シリーズ1本に絞ってから並べる */
    var seenSeries = {}, uniq = [];
    scored.forEach(function (x) {
      var k = x.w.s || x.w.v;
      if (seenSeries[k]) return;
      seenSeries[k] = 1;
      uniq.push(x);
    });
    scored = uniq.slice(0, n);
    if (!scored.length) return '<p class="empty">薦められる作品が見つかりませんでした。</p>';
    return scored.map(function (x) {
      var w = x.w;
      var ts = (tr.works[w.v] || []).filter(function (i) { return weight[i]; }).slice(0, 3);
      return '<div class="rec">' +
        (w.c ? '<img class="thumb cover" src="' + w.c + '" alt="" loading="lazy">'
             : '<div class="thumb ph"><span>画像なし</span></div>') +
        '<div style="flex:1"><div class="hd">' +
        '<a class="t" href="' + BASE + w.u + '">' + esc(w.t) + '</a>' +
        '<span class="pct">一致 ' + Math.max(1, Math.min(99, Math.round(x.s / full * 100))) + '%</span></div>' +
        '<div class="m">' + [w.dj, w.p].filter(Boolean).map(esc).join('・') + '</div>' +
        '<div class="tags" style="margin-top:6px">' +
        ts.map(function (i) {
          return '<a class="tag tag-outline" href="' + BASE + (tr.urls[i] || '#') + '">' + tr.names[i] + '</a>';
        }).join('') + '</div>' +
        '<div style="display:flex;gap:8px;margin-top:8px">' +
        '<a class="btn btn-secondary" href="' + BASE + w.u + '">作品ページ</a>' +
        '<button type="button" class="btn btn-ghost" data-want="' + w.v + '">♡ 欲しい</button>' +
        '</div></div></div>';
    }).join('');
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* ---------------------------------------------------------------- MY棚 */
  function initMy() {
    var list = $('[data-my-list]');
    if (!list) return;
    var filter = 'owned', platFilter = 'all', editing = null;
    var idx = null, eds = null, tr = null, byVid = {};

    var modal = $('[data-my-modal]');
    var fields = {
      search: $('[data-my-search]'), hits: $('[data-my-hits]'), picked: $('[data-my-picked]'),
      plat: $('[data-my-plat]'), ed: $('[data-my-ed]'), date: $('[data-my-date]'),
      price: $('[data-my-price]'), read: $('[data-my-read]'), total: $('[data-my-total]'),
      shop: $('[data-my-shop]'), memo: $('[data-my-memo]')
    };
    var pickedVid = null, pickedStatus = 'owned';

    function ready() {
      return Promise.all([fetchJSON('index.json'), fetchJSON('editions.json'), fetchJSON('traits.json')])
        .then(function (r) {
          idx = r[0] || []; eds = r[1] || {}; tr = r[2];
          byVid = {};
          idx.forEach(function (w) { byVid[w.v] = w; });
        });
    }

    function statOf(vid) { return Shelf.get(vid); }

    function matches(vid) {
      var it = statOf(vid);
      if (!it) return false;
      if (filter === 'owned') return STATUS[it.s] && STATUS[it.s].owned;
      if (filter === 'backlog') return it.s === 'owned';
      return it.s === filter;
    }

    function rows() {
      var q = norm(($('[data-my-q]') || {}).value || '');
      var sort = ($('[data-my-sort]') || {}).value || 'added_desc';
      var out = Object.keys(Shelf.data).filter(matches).map(function (v) {
        return { v: v, it: Shelf.data[v], w: byVid[v] };
      }).filter(function (r) {
        if (platFilter !== 'all' && (!r.it.p || r.it.p.indexOf(platFilter) < 0)) return false;
        if (!q) return true;
        return norm((r.w && r.w.t) || r.it.t || '').indexOf(q) >= 0;
      });
      out.sort(function (a, b) {
        if (sort === 'date_desc') return (b.it.d || '').localeCompare(a.it.d || '');
        if (sort === 'date_asc') return (a.it.d || '9999').localeCompare(b.it.d || '9999');
        if (sort === 'price_desc') return (b.it.pr || 0) - (a.it.pr || 0);
        if (sort === 'title') return ((a.w && a.w.t) || '').localeCompare((b.w && b.w.t) || '', 'ja');
        return (b.it.at || 0) - (a.it.at || 0);
      });
      return out;
    }

    function paint() {
      ['owned', 'backlog', 'playing', 'cleared', 'want'].forEach(function (k) {
        var el = $('[data-n="' + k + '"]');
        if (el) el.textContent = Shelf.count(k);
      });
      $$('[data-stat]').forEach(function (b) {
        b.setAttribute('aria-pressed', String(b.getAttribute('data-stat') === filter));
      });
      var titles = { owned: '所持作品', backlog: '未プレイ', playing: 'プレイ中',
                     cleared: 'クリア済', want: '欲しい作品' };
      $('[data-my-title]').textContent = titles[filter];

      var rs = rows();
      $('[data-my-count]').textContent = rs.length + '件を表示';
      list.innerHTML = rs.length ? rs.map(rowHTML).join('')
        : '<p class="empty">この条件に当てはまる作品がありません。'
          + '作品ページの「プレイ状況」か、上の「＋ ゲームを登録」から追加できます。</p>';

      var backlog = Shelf.count('backlog');
      var callout = $('[data-backlog]');
      if (callout) {
        callout.hidden = backlog === 0;
        if (backlog) {
          var oldest = Object.keys(Shelf.data)
            .filter(function (v) { return Shelf.data[v].s === 'owned' && Shelf.data[v].d; })
            .sort(function (a, b) { return Shelf.data[a].d.localeCompare(Shelf.data[b].d); })[0];
          $('[data-backlog-title]').textContent = '積みゲーが' + backlog + '本あります';
          $('[data-backlog-note]').textContent = oldest
            ? 'いちばん古い未プレイは『' + ((byVid[oldest] || {}).t || Shelf.data[oldest].t || '') +
              '』（' + Shelf.data[oldest].d + ' 購入）です。'
            : '購入日を記録すると、いちばん古い積みゲーが分かります。';
        }
      }
      var upd = $('[data-my-updated]');
      if (upd) {
        var n = Object.keys(Shelf.data).length;
        upd.textContent = n ? '登録 ' + n + '件' : '';
      }
      paintTaste();
    }

    function rowHTML(r) {
      var it = r.it, w = r.w || {};
      var st = STATUS[it.s] || { label: it.s, badge: 'tag-neutral' };
      var read = Number(it.rd || 0), total = Number(it.tt || 0);
      var pct = total ? Math.round(read / total * 100) : (it.s === 'cleared' ? 100 : 0);
      var buy = it.d ? [it.d, it.sh, it.pr ? yen(it.pr) : ''].filter(Boolean).join('　') + 'で購入'
                     : '購入の記録なし';
      return '<div class="myitem">' +
        (w.c ? '<img class="thumb cover" src="' + w.c + '" alt="" loading="lazy">'
             : '<div class="thumb ph"><span>画像なし</span></div>') +
        '<div class="body"><div class="hd">' +
        '<a class="t" href="' + BASE + (w.u || '#') + '">' + esc(w.t || it.t || r.v) + '</a>' +
        '<span class="tag ' + st.badge + '">' + st.label + '</span></div>' +
        '<div class="m">' + esc([it.p, it.e].filter(Boolean).join(' ') || '版の記録なし') + '</div>' +
        '<div class="m">' + esc(buy) + '</div>' +
        '<div class="prog"><span>' + (total ? read + '/' + total + 'ルート読了' : '進捗の記録なし') +
        '</span><span class="bar"><i style="width:' + pct + '%"></i></span></div>' +
        (it.m ? '<div class="memo">' + esc(it.m) + '</div>' : '') +
        '<div style="display:flex;gap:8px;margin-top:10px">' +
        '<button type="button" class="btn btn-secondary" data-my-edit="' + r.v + '">状態を編集</button>' +
        '<a class="btn btn-ghost" href="' + BASE + (w.u || '#') + '">作品ページ</a>' +
        '</div></div></div>';
    }

    function paintTaste() {
      if (!tr) return;
      var p = tasteProfile(tr);
      var lead = $('[data-my-trait-lead]');
      var box = $('[data-my-traits]');
      var rec = $('[data-my-rec]');
      if (!p.owned.length) {
        lead.textContent = '棚に所持作品を登録すると、ここに属性の集計が出ます。';
        box.innerHTML = '';
        rec.innerHTML = '<p class="empty">MY棚が空です。</p>';
        return;
      }
      lead.textContent = '所持' + p.owned.length + '本のキャラクター属性を集計しました。';
      var top = p.rank.slice(0, 6);
      var max = top[0].n;
      box.innerHTML = top.map(function (t, i) {
        return '<div class="trow" style="grid-template-columns:24px 128px minmax(0,1fr) auto">' +
          '<span style="font-family:var(--font-heading);font-weight:700;color:var(--color-accent-700)">' +
          (i + 1) + '</span>' +
          '<a class="nm" href="' + BASE + (tr.urls[t.i] || '#') + '">' + tr.names[t.i] + '</a>' +
          pctBar(Math.round(t.n / max * 100)) +
          '<span class="ct">' + t.n + '作品</span></div>';
      }).join('');
      rec.innerHTML = recommendHTML(tr, idx, p, 4);
    }

    /* --- 登録ダイアログ --- */
    function openModal(vid) {
      editing = vid || null;
      pickedVid = vid || null;
      var it = vid ? Shelf.get(vid) : null;
      pickedStatus = (it && it.s) || 'owned';
      $('[data-my-modal-title]').textContent = vid ? '記録を編集' : 'ゲームを登録';
      $('[data-my-delete]').hidden = !vid;
      fields.search.value = '';
      fields.hits.innerHTML = '';
      fields.picked.textContent = vid ? ((byVid[vid] || {}).t || (it && it.t) || vid) : '';
      fields.date.value = (it && it.d) || '';
      fields.price.value = (it && it.pr) || '';
      fields.read.value = (it && it.rd) || '';
      fields.total.value = (it && it.tt) || '';
      fields.shop.value = (it && it.sh) || '';
      fields.memo.value = (it && it.m) || '';
      paintStatusSeg();
      fillEditions(vid, it);
      if (modal.showModal) modal.showModal(); else modal.setAttribute('open', '');
      (vid ? fields.date : fields.search).focus();
    }
    function closeModal() {
      if (modal.close) modal.close(); else modal.removeAttribute('open');
    }
    function paintStatusSeg() {
      $$('button', $('[data-my-status]')).forEach(function (b) {
        b.setAttribute('aria-pressed', String(b.getAttribute('data-status') === pickedStatus));
      });
    }
    function fillEditions(vid, it) {
      var map = (vid && eds[vid]) || {};
      var plats = Object.keys(map);
      fields.plat.innerHTML = plats.length
        ? plats.map(function (p) { return '<option>' + esc(p) + '</option>'; }).join('')
        : '<option value="">（機種の記録なし）</option>';
      if (it && it.p && plats.indexOf(it.p) >= 0) fields.plat.value = it.p;
      fillEdOptions(map, it);
      fields.plat.onchange = function () { fillEdOptions(map, null); };
    }
    function fillEdOptions(map, it) {
      var list = map[fields.plat.value] || [];
      fields.ed.innerHTML = list.length
        ? list.map(function (x) { return '<option>' + esc(x) + '</option>'; }).join('')
        : '<option value="">（版の記録なし）</option>';
      if (it && it.e && list.indexOf(it.e) >= 0) fields.ed.value = it.e;
    }

    fields.search.addEventListener('input', function () {
      var q = norm(fields.search.value);
      if (q.length < 1) { fields.hits.innerHTML = ''; return; }
      var hits = idx.filter(function (w) {
        return norm(w.t).indexOf(q) >= 0 || (w.l || '').indexOf(q) >= 0;
      }).slice(0, 12);
      fields.hits.innerHTML = hits.map(function (w) {
        return '<button type="button" class="btn btn-secondary btn-block" data-pick="' + w.v + '">' +
          esc(w.t) + '　<span class="text-muted">' + esc(w.dj || '') + '</span></button>';
      }).join('');
    });
    fields.hits.addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-pick]');
      if (!b) return;
      pickedVid = b.getAttribute('data-pick');
      fields.picked.textContent = (byVid[pickedVid] || {}).t || pickedVid;
      fields.hits.innerHTML = '';
      fields.search.value = '';
      fillEditions(pickedVid, null);
    });
    $$('button', $('[data-my-status]')).forEach(function (b) {
      b.addEventListener('click', function () {
        pickedStatus = b.getAttribute('data-status');
        paintStatusSeg();
      });
    });
    $('[data-my-save]').addEventListener('click', function () {
      if (!pickedVid) { alert('作品を選んでください。'); fields.search.focus(); return; }
      Shelf.set(pickedVid, {
        s: pickedStatus, t: (byVid[pickedVid] || {}).t || '',
        p: fields.plat.value || '', e: fields.ed.value || '',
        d: fields.date.value || '', pr: fields.price.value ? Number(fields.price.value) : '',
        rd: fields.read.value ? Number(fields.read.value) : '',
        tt: fields.total.value ? Number(fields.total.value) : '',
        sh: fields.shop.value || '', m: fields.memo.value || ''
      });
      closeModal();
    });
    $('[data-my-delete]').addEventListener('click', function () {
      if (editing && confirm('この作品を棚から削除しますか？')) {
        Shelf.remove(editing);
        closeModal();
      }
    });
    $$('[data-my-close]').forEach(function (b) { b.addEventListener('click', closeModal); });
    $('[data-my-add]').addEventListener('click', function () { openModal(null); });
    list.addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-my-edit]');
      if (b) openModal(b.getAttribute('data-my-edit'));
    });
    $$('[data-stat]').forEach(function (b) {
      b.addEventListener('click', function () { filter = b.getAttribute('data-stat'); paint(); });
    });
    var bg = $('[data-backlog-go]');
    if (bg) bg.addEventListener('click', function () { filter = 'backlog'; platFilter = 'all'; paint(); });
    var q = $('[data-my-q]');
    if (q) q.addEventListener('input', paint);
    var so = $('[data-my-sort]');
    if (so) so.addEventListener('change', paint);

    /* 書き出し・読み込み。端末を移るときの唯一の手段なので必ず用意する */
    $('[data-my-export]').addEventListener('click', function () {
      var blob = new Blob([JSON.stringify(Shelf.data, null, 2)], { type: 'application/json' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'otomedana-shelf.json';
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    });
    var file = $('[data-my-file]');
    $('[data-my-import]').addEventListener('click', function () { file.click(); });
    file.addEventListener('change', function () {
      var f = file.files[0];
      if (!f) return;
      f.text().then(function (t) {
        var got;
        try { got = JSON.parse(t); } catch (err) { alert('読み込めませんでした。'); return; }
        if (!got || typeof got !== 'object') { alert('形式が違います。'); return; }
        if (!confirm('今の棚に上書きします。よろしいですか？')) return;
        Shelf.data = got;
        Shelf.save();
      });
    });

    ready().then(function () {
      var m = /[?&]edit=([^&]+)/.exec(location.search);
      paint();
      if (m) openModal(decodeURIComponent(m[1]));
    });
    document.addEventListener('shelf:change', paint);
  }

  /* おすすめの「♡ 欲しい」はどのページからでも効くよう、まとめて拾う */
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-want]');
    if (!b) return;
    var vid = b.getAttribute('data-want');
    if (Shelf.get(vid)) Shelf.remove(vid); else Shelf.set(vid, { s: 'want' });
  });

  /* ---------------------------------------------------------------- 起動 */
  function boot() {
    paintHeaderCount();
    paintCards();
    initListFilter();
    initHomeSearch();
    initBuy();
    initShelfPanel();
    initTaste();
    initMy();
    document.addEventListener('shelf:change', function () {
      paintHeaderCount();
      paintCards();
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
