/* トップページの検索と、索引ページのページ内絞り込み。
   index.json だけで動き、検索欄に触れたとき suggest.json を追加で読む。

   照合はすべて事前に正規化した文字列に対して行う。
   かな・カナ・漢字・ローマ字のどれで入力しても引けるようにしている。

   検索結果は50件ずつ。条件が何も無いあいだは結果を出さず、
   カテゴリの入口と新着だけが見えている状態を初期表示にしている。 */
/* 表記ゆれの吸収。トップの検索と、索引ページのページ内絞り込みの
   両方から使うのでファイルの先頭に出してある。 */
var OD = (function () {

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


  // 作品名によく出る英単語の読み。ローマ字変換では「clock → ころく」に
  // なってしまうため、英単語は辞書で読みを与える。
  // 日本語のローマ字と紛らわしい語（no / to / de / na など）は入れない。
  var EN = {
    the:'ザ', of:'オブ', and:'アンド', with:'ウィズ', for:'フォー', in:'イン', on:'オン',
    my:'マイ', all:'オール', even:'イーブン', if:'イフ', un:'アン', one:'ワン', ones:'ワンズ',
    love:'ラブ', lover:'ラバー', lovers:'ラバーズ', loved:'ラブド',
    wonderful:'ワンダフル', wonder:'ワンダー', world:'ワールド',
    starry:'スターリー', star:'スター', sky:'スカイ', princess:'プリンセス', prince:'プリンス',
    tokyo:'トウキョウ', boys:'ボーイズ', boy:'ボーイ', girl:'ガール', girls:'ガールズ',
    disc:'ディスク', diabolik:'ディアボリック', sweet:'スウィート', side:'サイド',
    days:'デイズ', day:'デイ', after:'アフター', heart:'ハート', hearts:'ハーツ',
    spring:'スプリング', summer:'サマー', autumn:'オータム', winter:'ウィンター',
    amnesia:'アムネシア', dynamic:'ダイナミック', chord:'コード', feat:'フィーチャリング',
    lost:'ロスト', darling:'ダーリン', honey:'ハニー', dear:'ディア', storm:'ストーム',
    halloween:'ハロウィン', wedding:'ウェディング', norn:'ノルン', school:'スクール',
    code:'コード', realize:'リアライズ', mystic:'ミスティック', sweat:'スウェット',
    tears:'ティアーズ', symphony:'シンフォニー', life:'ライフ', kiss:'キス',
    engagement:'エンゲージメント', desert:'デザート', vitamin:'ビタミン', cross:'クロス',
    road:'ロード', supernova:'スーパーノヴァ', club:'クラブ', snow:'スノー',
    future:'フューチャー', dark:'ダーク', land:'ランド', beyond:'ビヨンド', time:'タイム',
    black:'ブラック', white:'ホワイト', money:'マネー', glass:'グラス', romeo:'ロミオ',
    juliet:'ジュリエット', alice:'アリス', bad:'バッド', good:'グッド', marginal:'マージナル',
    birthday:'バースデー', song:'ソング', eden:'エデン', klap:'クラップ', kind:'カインド',
    punish:'パニッシュ', another:'アナザー', apple:'アップル', dance:'ダンス',
    devils:'デビルズ', devil:'デビル', collar:'カラー', malice:'マリス', charade:'シャレード',
    maniacs:'マニアックス', bustafellows:'バスタフェロウズ', tempest:'テンペスト',
    clock:'クロック', zero:'ゼロ', marriage:'マリッジ', special:'スペシャル',
    prologue:'プロローグ', epilogue:'エピローグ', mind:'マインド', backlash:'バックラッシュ',
    apprentice:'アプレンティス', magician:'マジシャン', double:'ダブル', reaction:'リアクション',
    beat:'ビート', red:'レッド', blue:'ブルー', green:'グリーン', gold:'ゴールド',
    silver:'シルバー', moon:'ムーン', sun:'サン', dream:'ドリーム', memory:'メモリー',
    memories:'メモリーズ', story:'ストーリー', tale:'テイル', magic:'マジック',
    garden:'ガーデン', rose:'ローズ', blood:'ブラッド', night:'ナイト', king:'キング',
    queen:'クイーン', knight:'ナイト', angel:'エンジェル', god:'ゴッド', fire:'ファイア',
    water:'ウォーター', wind:'ウィンド', light:'ライト', shadow:'シャドウ',
    secret:'シークレット', promise:'プロミス', forever:'フォーエバー', eternal:'エターナル',
    first:'ファースト', last:'ラスト', new:'ニュー', real:'リアル', root:'ルート',
    gate:'ゲート', key:'キー', door:'ドア', letter:'レター', message:'メッセージ',
    voice:'ボイス', sound:'サウンド', music:'ミュージック', party:'パーティー',
    game:'ゲーム', card:'カード', box:'ボックス', complete:'コンプリート',
    edition:'エディション', limited:'リミテッド', portable:'ポータブル', twin:'ツイン',
    pack:'パック', best:'ベスト', plus:'プラス', full:'フル', mini:'ミニ',
    deluxe:'デラックス', remake:'リメイク', pia:'ピア', vitaminx:'ビタミンエックス',
    vitaminz:'ビタミンゼット', logical:'ロジカル', record:'レコード', noah:'ノア',
    mirage:'ミラージュ', mystique:'ミスティーク', vibes:'バイブス', chain:'チェーン',
    dunk:'ダンク', basketball:'バスケットボール', nursery:'ナーサリー', rhyme:'ライム',
    memorial:'メモリアル', tennis:'テニス', cinderella:'シンデレラ', happy:'ハッピー',
    season:'シーズン', drops:'ドロップス', drop:'ドロップ', fortune:'フォーチュン',
    maria:'マリア', miss:'ミス', debut:'デビュー', wand:'ワンド', arabians:'アラビアンズ',
    chaos:'カオス', lineage:'リネージュ', destination:'デスティネーション',
    unlimited:'アンリミテッド', sequel:'シークエル', prince:'プリンス', lost:'ロスト'
  };

  function romajiWord(s) {
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

  // 単語ごとに、英単語なら辞書、そうでなければローマ字として読みを作る
  function readingOf(s) {
    if (!s) return '';
    var ws = s.toLowerCase().match(/[a-z']+/g);
    if (!ws) return '';
    var out = '';
    for (var i = 0; i < ws.length; i++) {
      var w = ws[i].replace(/'/g, '');
      out += EN[w] || romajiWord(w);
    }
    return norm(out);
  }

  // 発売年の区分。site_config.py の YEAR_BUCKETS と対応させる
  function inYear(iso, bucket) {
    if (!bucket) return true;
    var y = parseInt((iso || '').slice(0, 4), 10) || 0;
    if (bucket === '2021-') return y >= 2021;
    if (bucket === '2016-2020') return y >= 2016 && y <= 2020;
    if (bucket === '2011-2015') return y >= 2011 && y <= 2015;
    if (bucket === '-2010') return y > 0 && y <= 2010;
    return true;
  }

  return { norm: norm, readingOf: readingOf, inYear: inYear };
})();

(function () {
  var BP = window.BASE_PATH || '';
  var norm = OD.norm, readingOf = OD.readingOf, inYear = OD.inYear;
  var root = document.getElementById('app');
  if (!root) return;

  var DATA = null, SUGGEST = null;
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
    var yr = el('select', { id: 'f-year' }); yr.appendChild(option('', '発売年：すべて'));
    ['2021-', '2016-2020', '2011-2015', '-2010'].forEach(function (y) { yr.appendChild(option(y, y)); });
    var ag = el('select', { id: 'f-age' }); ag.appendChild(option('', '対象年齢：すべて'));
    (DATA.vocab.age || []).forEach(function (a) { ag.appendChild(option(a.v, a.n)); });
    var st = el('select', { id: 'f-sort' });
    [['new', '発売日が新しい順'], ['old', '発売日が古い順'],
     ['rate', '評価が高い順'], ['pop', '票数が多い順']].forEach(function (o) {
      st.appendChild(option(o[0], o[1]));
    });
    [pl, yr, ag, st].forEach(function (x) { row.appendChild(x); });
    panel.appendChild(row);

    var rs = el('button', { type: 'button', id: 'f-reset', class: 'reset' });
    rs.textContent = '条件をクリア';
    panel.appendChild(rs);

    root.appendChild(panel);

    // 検索結果。条件が何も無いあいだは節ごと隠しておき、
    // カテゴリの入口と新着だけが見えている状態を初期表示にする
    var res = el('section', { id: 'f-results', hidden: 'hidden' });
    res.appendChild(el('h2', { text: '検索結果' }));
    res.appendChild(el('p', { class: 'count', id: 'f-count' }));
    res.appendChild(el('ul', { class: 'cards', id: 'f-out' }));
    res.appendChild(el('nav', { class: 'pager', id: 'f-pager',
                                'aria-label': '検索結果のページ' }));
    root.appendChild(res);

    // 打鍵のたびに全件を走らせない
    var timer = null;
    q.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () { onType(); render(); }, 120);
    });
    q.addEventListener('keydown', onKey);
    document.addEventListener('click', function (ev) { if (!box.contains(ev.target)) hideSug(); });
    [pl, yr, ag, st].forEach(function (x) {
      x.addEventListener('change', function () { render(); });
    });
    rs.addEventListener('click', function () {
      [q, pl, yr, ag].forEach(function (x) { x.value = ''; });
      st.value = 'new';
      document.getElementById('m-all').checked = true;
      hideSug(); render();
    });
    render();
  }

  /* ---------------- 追加読み込みと索引づくり ---------------- */

  function prepare() {
    // 照合用の文字列を1度だけ作る
    DATA.items.forEach(function (it, i) {
      it._i = i;
      it._n = norm(it.t);
      it._l = norm(it.l);
      it._r = readingOf(it.l || it.t); // 原題がラテン文字なら原題から読みを作る
    });
    (DATA.vocab.cv || []).forEach(function (v) { v._n = norm(v.n); });
    (DATA.vocab.staff || []).forEach(function (v) { v._n = norm(v.n); });

    SUGGEST.forEach(function (s) {
      s._n = norm(s.n);
      s._k = norm(s.k);
      s._r = readingOf(s.k || s.n);  // ローマ字・英単語から起こしたかな
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

  /* ---------------- 絞り込みの実行と表示 ----------------
     条件が何も無いあいだは検索結果を出さず、カテゴリの入口と新着だけを見せる。
     結果は50件ずつに切り、下にページ番号を並べる。 */

  var PAGE = 1;
  var PER = 50;

  // 「検索した」とみなす条件。並び順だけ変えても検索にはしない
  function active() {
    return !!(norm(val('f-q')) || val('f-plat') !== '' || val('f-year') !== ''
              || val('f-age') !== '');
  }

  function drawPager(pages) {
    var nav = document.getElementById('f-pager');
    nav.innerHTML = '';
    if (pages <= 1) return;
    for (var i = 1; i <= pages; i++) {
      (function (n) {
        var b = el('button', { type: 'button', text: String(n) });
        if (n === PAGE) { b.className = 'on'; b.setAttribute('aria-current', 'page'); }
        b.addEventListener('click', function () {
          PAGE = n;
          render(true);
          document.getElementById('f-results').scrollIntoView(true);
        });
        nav.appendChild(b);
      })(i);
    }
  }

  function render(keepPage) {
    if (!keepPage) PAGE = 1;       // 条件が変わったら1ページ目に戻す
    var res = document.getElementById('f-results');
    var br = document.getElementById('browse');
    var on = active();
    res.hidden = !on;
    if (br) br.hidden = on;
    if (!on) {
      document.getElementById('f-out').innerHTML = '';
      document.getElementById('f-pager').innerHTML = '';
      document.getElementById('f-count').textContent = '';
      return;
    }

    var q = norm(val('f-q'));
    var pl = val('f-plat'), yr = val('f-year'), ag = val('f-age');

    var m = mode();                 // DOM参照は1回だけ
    var out = [];
    for (var n = 0; n < DATA.items.length; n++) {
      var it = DATA.items[n], why = null;
      if (q) { why = textMatch(it, q, m); if (!why) continue; }
      if (pl !== '' && it.p.indexOf(+pl) < 0) continue;
      if (ag !== '' && it.a !== ag) continue;
      if (!inYear(it.r, yr)) continue;
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

    var pages = Math.max(1, Math.ceil(out.length / PER));
    if (PAGE > pages) PAGE = pages;
    var from = (PAGE - 1) * PER;
    var page = out.slice(from, from + PER);
    document.getElementById('f-count').textContent = out.length
      ? '全' + out.length + '件中 ' + (from + 1) + '〜' + (from + page.length) + '件目'
      : '該当する作品はありませんでした。';

    var ul = document.getElementById('f-out');
    var frag = document.createDocumentFragment();
    page.forEach(function (o) {
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
    ul.innerHTML = '';
    ul.appendChild(frag);
    drawPager(pages);
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

/* カテゴリ索引ページ（/cv/ /maker/ /series/ …）のページ内絞り込み。
   項目はサーバ側で書き出した実体のHTMLなので、JSが無くても一覧は読める。
   照合はトップと同じ OD.norm / OD.readingOf を使い、
   漢字表記の名前でも、かな・カタカナ・ローマ字で引けるようにする。 */
(function () {
  var box = document.querySelector('[data-idx]');
  if (!box) return;
  var input = box.querySelector('.idx-filter');
  var count = box.querySelector('.idx-count');
  if (!input) return;

  var items = [].slice.call(box.querySelectorAll('li[data-k]'));
  var secs = [].slice.call(box.querySelectorAll('[data-sec]'));
  var tops = [].slice.call(box.querySelectorAll('[data-top]'));
  var jump = box.querySelector('.idx-jump');
  var empty = document.createElement('p');
  empty.className = 'idx-empty';
  empty.hidden = true;
  box.appendChild(empty);

  // スラッグが名前の読みでないカテゴリ（タグ・属性は英訳）では
  // ローマ字からかなを起こさない。起こしても雑音にしかならないため
  var useReading = box.hasAttribute('data-reading');
  items.forEach(function (li) {
    var k = li.getAttribute('data-k') || '';
    li._n = OD.norm(li.textContent);   // 表示名（漢字・かな）
    li._k = OD.norm(k);                // スラッグ
    li._r = useReading ? OD.readingOf(k) : '';   // ローマ字から起こしたかな
    // 同じ人が複数の節に出ることがある（冒頭の抜粋、複数の役割を持つスタッフ）。
    // 件数はリンク先で重複を落として数える
    var a = li.querySelector('a');
    li._u = (a && a.getAttribute('href')) || '';
  });

  var timer = null;
  function apply() {
    var q = OD.norm(input.value);
    var seen = {}, hits = 0;
    items.forEach(function (li) {
      var on = !q || li._n.indexOf(q) >= 0 || li._k.indexOf(q) >= 0 ||
               (li._r && li._r.indexOf(q) >= 0);
      li.hidden = !on;
      if (on && !seen[li._u]) { seen[li._u] = 1; hits++; }
    });
    // 絞り込み中は「作品数の多い順」の抜粋と五十音の飛び先を隠す（重複して紛らわしいため）
    tops.forEach(function (x) { x.hidden = !!q; });
    if (jump) jump.hidden = !!q;
    secs.forEach(function (sec) {
      var vis = [].slice.call(sec.querySelectorAll('li[data-k]'))
                  .some(function (li) { return !li.hidden; });
      sec.hidden = !vis;
    });
    count.textContent = q ? hits + '件' : '';
    empty.hidden = !(q && hits <= 0);
    if (!empty.hidden) empty.textContent = '「' + input.value + '」に当てはまるものはありませんでした。';
  }

  input.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(apply, 100);
  });
  // 「?q=」で開いたときにも効かせる
  var p = new URLSearchParams(location.search).get('q');
  if (p) { input.value = p; apply(); }
})();

/* 一覧ページ（声優・スタッフ）のカードの絞り込みと並べ替え。
   カードはサーバ側で書き出した実体のHTMLなので、JSが無くても一覧は読める。
   選択肢もそのページにある値だけをサーバ側で出してあるので、空振りしない。 */
(function () {
  var box = document.querySelector('[data-list]');
  if (!box) return;
  var tools = box.querySelector('[data-list-tools]');
  if (!tools) return;

  var count = tools.querySelector('.list-count');
  var reset = tools.querySelector('.list-reset');
  var sels = {};
  [].slice.call(tools.querySelectorAll('select[data-f]')).forEach(function (s) {
    sels[s.getAttribute('data-f')] = s;
    s.addEventListener('change', apply);
  });

  var lists = [].slice.call(box.querySelectorAll('ul.cards'));
  lists.forEach(function (ul) { ul._items = [].slice.call(ul.querySelectorAll('li[data-r]')); });
  var total = lists.reduce(function (n, ul) { return n + ul._items.length; }, 0);

  function val(name) { return sels[name] ? sels[name].value : ''; }
  function num(li, attr) {
    var x = parseFloat(li.getAttribute(attr));
    return isNaN(x) ? -1 : x;      // 未評価・未投票は最後に回す
  }

  function apply() {
    var pl = val('plat'), yr = val('year'), ag = val('age'), st = val('sort') || 'new';
    var shown = 0;
    lists.forEach(function (ul) {
      var keep = [];
      ul._items.forEach(function (li) {
        var on = true;
        if (pl && (li.getAttribute('data-p') || '').split(' / ').indexOf(pl) < 0) on = false;
        if (on && yr && li.getAttribute('data-y') !== yr) on = false;
        if (on && ag && li.getAttribute('data-a') !== ag) on = false;
        li.hidden = !on;
        if (on) keep.push(li);
      });
      shown += keep.length;
      keep.sort(function (a, b) {
        if (st === 'rate') return num(b, 'data-g') - num(a, 'data-g');
        if (st === 'pop') return num(b, 'data-n') - num(a, 'data-n');
        // 発売日が未定のものは、どちらの並びでも最後に回す
        var no = (st === 'old') ? '9999' : '';
        var x = a.getAttribute('data-r') || no, y = b.getAttribute('data-r') || no;
        if (st === 'old') return x < y ? -1 : x > y ? 1 : 0;
        return x > y ? -1 : x < y ? 1 : 0;
      });
      keep.forEach(function (li) { ul.appendChild(li); });
      // 「出演」「スタッフとしての参加」は、中身が消えたら見出しごと隠す
      ul.hidden = !keep.length;
      var h = ul.previousElementSibling;
      if (h && h.tagName === 'H2') h.hidden = !keep.length;
    });
    var filtered = !!(pl || yr || ag);
    count.textContent = filtered ? shown + '件 / ' + total + '件' : '';
    reset.hidden = !filtered && st === 'new';
  }

  reset.addEventListener('click', function () {
    for (var k in sels) { sels[k].value = (k === 'sort' ? 'new' : ''); }
    apply();
  });
  apply();
})();
