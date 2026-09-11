---
name: x-character-intro
description: オトメゲームDBのキャラクターページに載せる「公式Xのキャラクター紹介ポスト」を探して登録する作業。「Xのキャラ紹介ポストを探して」「紹介ポストの収集を続けて」「この作品の紹介ポストある？」「収集一覧と見つからなかった一覧を出して」などのときに使う。キャラ名でアカウント内を1人ずつ検索し、x_posts.py で取り込み・検証・サイト反映まで行う。誤った検索方法で取りこぼした過去があるため、手順を必ずこの通りにたどること。
---

# 公式Xのキャラクター紹介ポストを集める

3709人のキャラクターそれぞれについて、**公式アカウントが投稿したそのキャラの紹介ポスト**を1件見つけて
`corrections/x_posts.tsv` に登録し、サイトのキャラクターページに埋め込む。

## 絶対に守ること

### 1. Xへのアクセスはゆっくり、間隔をランダムに

依頼者のアカウントは過去にクロールで画像読み込み制限を受けている。X の規約上も自動アクセスは禁止。

- 検索と検索のあいだは **8〜9秒程度、毎回ばらつかせる**（`computer` の `wait` を使う）
- メディア欄のスクロールも同じ。等間隔で連打しない
- **中断の合図**: 画像が読み込まれない／レート制限のバナーが出る。見えたら即座に止めて依頼者に報告する
- ページが固まったら90秒ほど置いてアカウントページを開き直し、画像が出るか確認してから再開する

### 2. JavaScript でスクロールしても X は追加読み込みしない

`window.scrollBy` では新しいポストが読み込まれない。同じ検索が
JSスクロールで4件、実際のホイール操作で15件になった実測がある。
**続きを読む必要があるときは `computer` の `scroll`（実ホイール）を使う。**
ただしスクロールは毎回スクリーンショットが返って高くつくので、次の方法を優先する。

### 3. 見出し語ではなく、キャラクター名で1人ずつ引く

「紹介」「キャラクター紹介」で検索すると X の索引の癖で取りこぼす。
冬園サクリフィスは「レジス」0件・「ルペルティエ」4件だった。
**1キャラ1検索**が最も確実で、スクロール不要なので軽い。

```bash
python3 scripts/x_queries.py v49601
```

未収集キャラごとに検索URLを出す。名前は区切って特徴のある断片を使う
（「レジス・ド・ルペルティエ」なら「ルペルティエ」）。

### 4. アカウント内検索は新しい4件しか返らない

`from:アカウント 紹介` は、そのアカウントの「紹介」を含むポストのうち
**新しい順に4件前後しか出さない**。グッズ紹介やブログ更新を頻繁に流している
アカウントだと、何年も前のキャラ紹介の連投がその下に埋もれて見えない。

ラディアンテイルで実際にこれが起きた。`from:RT_x_info 紹介` はブログ更新4件しか
返さず、「紹介ポストは無い」と判断して誕生日ポストで登録してしまった。
あとから `from:RT_x_info 攻略キャラクター紹介` で引いたら
【IF STORY 攻略キャラクター紹介】が2件出てきた。

**「無い」と判断する前に、言い回しを変えて複数回引くこと。**

```
from:アカウント キャラクター紹介
from:アカウント 攻略キャラクター紹介
from:アカウント 登場人物紹介
from:アカウント サブキャラクター紹介
from:アカウント "に登場するキャラクター"
```

連投が見つかったら `until:YYYY-MM-DD` で窓を切るか、実ホイールでスクロールして
最後まで辿る。1回の検索で全員ぶんが出ることはまずない。

### 5 登録済みのアカウントも疑う

`x_accounts.tsv` にアカウントが入っているからといって、それが正しいとは限らない。
**ブランド名が似ているだけの別会社**が入っていたことがある。

ボルテージの恋愛ドラマアプリ10本に、アイディアファクトリーのブランドである
@AmuLit_JP が入っていた。名前が似ているうえ、@AmuLit_JP は2023年開設なので
2011〜2018年の作品の投稿があるわけがない。正しくは @volkoi_official
（ボル恋シリーズ公式）で、そこには作品名つきの誕生日ポストが全キャラぶんあった。
差し替えたら50人以上が一気に埋まった。

収集ゼロのアカウントを見つけたら、先に **DBのブランド名（`work.brand`）と
アカウントの運営元が一致するか**を確かめる。一致しないなら正しい公式を探し直す。
作品名でユーザー検索（`https://x.com/search?q=<作品名>&f=user`）すると早い。

### 6 1アカウントが複数作を兼ねるときは姓の衝突に注意

シリーズ公式やレーベル公式は何十本もの作品を持つ。**姓だけが同じ別作品のキャラ**に
貼ってしまう事故が起きる。

@OtomateWeb の「◆ミツチの参謀『大蛇 -おろち-』2810歳」を、ヒイロノカケラの
「大蛇 凌」に貼りそうになった。まったくの別人。

取り込んだあとは必ず `_queue_x.tsv` の note 列（本文の先頭）を読んで、
**その本文がその作品のその人の話をしているか**を目で確かめる。`match` 列が
`unique`（部分一致だが作品内で1人だけ）の行がとくに危ない。

## 手順

### 手順0 対象を選ぶ

```bash
python3 scripts/x_posts.py report
```

作品ごとの残りは次で出す。2012年以降・アカウント登録済みを優先する。
2011年以前は発売時点でXに公式アカウントが無いことが多く、実りが薄い。

```bash
python3 - <<'PY'
import sys, os, sqlite3, collections
sys.path.insert(0,"scripts")
import x_posts as X
from common import DATA
best=set(X.best_posts()); none_ids={r["id"] for r in X.read_tsv(X.NONE)}
acc={r["vid"]:r["account"].strip().split()[0] for r in X.read_tsv(X.ACCOUNTS) if r["account"].strip()}
con=sqlite3.connect(os.path.join(DATA,"site2.db"))
chars=collections.defaultdict(set)
for vid,cid in con.execute("select main_vid,cid from character"): chars[vid].add(cid)
for vid,cid in con.execute("select vid,cid from character_work"): chars[vid].add(cid)
w={r[0]:(r[1],(r[2] or "")[:10]) for r in con.execute("select vid,title,released from work")}
rows=[]
for v,cs in chars.items():
    if v in none_ids or v not in acc: continue
    m=len(cs-best); t,rel=w.get(v,("?",""))
    if m and rel[:4]>="2012": rows.append((m,rel,v,t,acc[v]))
for r in sorted(rows,key=lambda r:(-r[0],r[1]))[:30]:
    print("%2d人 %-10s %-8s %-30s %s"%r)
PY
```

### 手順1 アカウントの開設時期を確かめる

**ゲームの発売より後に作られたアカウントには、発売時の紹介ポストは無い。**
@AmuLit_JP は2023年10月開設なのに2011〜2016年の6作品に登録されていた。
@Haruka6Official のように存在しないアカウントが登録されていることもある。

`https://x.com/<handle>` を開いて「◯年◯月からXを利用しています」を読む。
誤りを見つけたら `corrections/x_accounts.tsv` を直し、根拠を note 列に書く。

### 手順2 キャラ名で検索して候補を拾う

`x_queries.py` が出したURLを `browser_batch` で3〜4本ずつ開く。
各ページで次を実行して、ポストIDと本文の先頭を取る。スクロールは不要。

```javascript
(()=>{const h={};document.querySelectorAll('article').forEach(a=>{
const t=a.querySelector('a[href*="/status/"]');if(!t)return;
const m=t.href.match(/status\/(\d+)/);if(!m)return;
h[m[1]]=(a.querySelector('div[data-testid="tweetText"]')||{innerText:''})
.innerText.replace(/\n/g,'|').slice(0,50)});
return Object.entries(h).map(([k,v])=>k+' '+v).join('\n')+'\n#'+Object.keys(h).length})()
```

連投シリーズを見つけたら、その見出し語で検索し直すと一気に揃う。
X の検索は1クエリ3〜5件で打ち切られるので、`until:YYYY-MM-DD` を1〜3日ずつ
ずらして窓を切ると連投の全体が取れる。

### 手順3 取り込む

URLを1行ずつ書いたテキストを作って渡す。cid も vid も書かなくてよい。

```bash
python3 scripts/x_posts.py intake ファイル.txt
python3 scripts/x_posts.py verify
```

`intake` は投稿者から作品を引き、本文からキャラを判定する。
`verify` が oEmbed で実在・投稿者・本文を確かめ、通ったものを `x_posts.tsv` に入れる。

拒否されたときのメッセージの意味:

| メッセージ | 意味 | 対応 |
|---|---|---|
| `@X が x_accounts.tsv に未登録` | 投稿者から作品を引けない | アカウントを登録する |
| `本文にキャラ名が出てこない` | 名前が画像内だけ、またはDB未収録のサブキャラ | 諦める |
| `N人が並ぶ一覧的な投稿` | 全キャラ列挙の告知 | 個別ポストを探す |
| `主題が別のキャラ（DB未収録）` | 見出しで別人を紹介していて、本文に別キャラ名が出ただけ | 正しい判定。放置してよい |

### 手順4 サイトに反映する

**`site2_build.py` は `site2.db` しか見ない。** 順番を守ること。

```bash
python3 scripts/x_posts.py embeds     # corrections/x_embeds.json を作り直す
python3 scripts/site2_build.py        # docs/v2 を再生成
```

キャラクターや価格を触ったときは、あいだに `python3 scripts/site2_db.py` を挟む。

### 手順5 記録する

```bash
python3 scripts/worklog.py "やったこと" --why "なぜ" --next "次"
git add -A && git commit -m "◯◯を収集した（N→M人）"
```

## どのポストを採るか

`x_posts.py` の `KIND_ORDER` が優先度。上ほど良い。

1. **プロフィール** — 【キャラクター紹介】【Character】【登場人物紹介】＋名前＋CV＋説明
2. **フルネーム** — フルネームとCVが書かれている
3. **その他**
4. **アイコン配布** — 紹介ポストが無いとき、誕生日より優先して使う
5. **誕生日** — 本当の最終手段
6. **イベントCG** — それ以外が何も無いときだけ
7. **販促** — 採らない。`best_posts()` が自動で除外する

**誕生日・アイコン配布・イベントCG で登録してよいのは、紹介ポストが無いと
確かめたあとだけ。** 確かめるとは、上の「言い回しを変えて複数回引く」を
やりきること。1回検索して出なかった、では確かめたことにならない。

**キャラクターを使った告知ポストは要らない。** 欲しいのはそのキャラを紹介するポスト。
ガチャ告知・グッズ紹介・雑誌掲載・コラボカフェのメニューは対象外。
画像の中にメッセージが入っているだけのものも違う。

**アカウント名に anime が入るものは要注意。** アニメ版の公式であってゲームの公式ではない。
ゲーム側の公式アカウントを優先する（NORN9 は古すぎてゲーム公式が無いので例外）。

## 実際に見つかった書式

同じ「紹介ポスト」でも作品ごとに書式がまるで違う。「紹介」という語が無いものも多い。

- `【キャラクター紹介】\n山科 瑛（やましな あきら）\nCV：#小林千晃`
- `【キャラクター紹介：狐射堂 遙（CV：村瀬 歩）】` — 見出しの中に名前
- `❄【キャラクター情報②】＜攻略キャラクター＞グラーディアの若き皇帝✧ レジス・ド・ルペルティエ`
- `【CHARACTER】神々紹介①\nアポロン・アガナ・ベレア\nギリシャ神話の太陽神。`
- `本日の村民紹介は久石珠燐さんです。` — 【】を使わない
- `▼望の幼馴染\n安藤礼二　(あんどう れいじ)\nＣＶ：鈴村健一`
- `【闇色の魔珠】キャラクター紹介プチSS　フィン篇`
- `🌃メインキャラ紹介🌃\n狙った獲物は逃さない世紀の大怪盗\nラミー・カリエール`
- `【月影の鎖-専門用語解説】『猪口ノア』` — 用語解説の体裁で人物を説明
- `🎉Happy Birthday🎉\n本日８月16日はカラミアの誕生日です` — 誕生日だが最終手段として可

## 誤配分に注意

紹介文には他のキャラの名前も出る。`subject_hit()` が「見出しのあと SUBJECT_SPAN 文字以内で、
行頭・区切り記号の直後・助詞「の」の直後から名前が始まるか」で主題を判定している。
新しい書式で取りこぼしたら、この関数を直してから取り込み直す。直したら必ず全件を点検する。

**誕生日ポストは特に取り違えやすい。** 姓だけ・愛称だけで書かれることが多く、
同じ姓の別人に貼ってしまう。実際に起きた2件。

- 「3/17は忍足謙也の誕生日」→ 忍足**侑士**に貼られた
- 「本日4月19日は不知火一樹の誕生日」→ **星月琥太郎**に貼られた（装飾見出しのせい）

`BIRTHDAY_HEAD`（`本日?◯月◯日は`）を主題宣言として読む規則を入れて直した。
この書式だけは、日付と名前のあいだに肩書きが入る
（「本日11月17日は四皇學園生徒会会長・鉤貫レム様の誕生日」）ので、
窓の中ならどこにあっても主題とみなす扱いにしてある。

**見出しのあとに ● で区分を書く書式も注意。**
「【神凪ノ杜　登場人物紹介】●龍神奇譚攻略対象キャラ 東雲」は oEmbed で改行が
消えると区分と名前がつながる。`SUBJECT_HEAD` が ● の行を見出しの一部として
読み飛ばすようにしてある。

```bash
python3 - <<'PY'
import sys; sys.path.insert(0,"scripts")
import x_posts as X
best=X.best_posts(); bad=[]
for r in X.read_tsv(X.POSTS):
    if best.get(r["cid"],{}).get("status_url")!=r["status_url"]: continue
    acct,sid=X.parse_status(r["status_url"]); d=X.oembed(sid,acct)
    if not d: continue
    if X.SUBJECT_HEAD.search(d["_text"]) and not X.subject_hit(d["_text"], r["character"]):
        bad.append((r["vid"], r["character"], d["_text"].replace("\n"," ")[:60]))
print("主題不一致:", len(bad))
for b in bad: print(" ", b)
PY
```

同じURLが2人以上に割り当てられていないかも見る。

```bash
python3 - <<'PY'
import sys, collections; sys.path.insert(0,"scripts")
import x_posts as X
u=collections.defaultdict(set)
for cid,r in X.best_posts().items(): u[r["status_url"]].add(r["character"])
for k,v in u.items():
    if len(v)>1: print(len(v), k, "、".join(sorted(v)))
PY
```

2人紹介・グループ紹介・DBの重複レコードなら正しいので、中身を見てから判断する。

## 「無い」と記録するとき

```bash
python3 scripts/x_posts.py none v12807 --note "根拠を具体的に書く"
```

**キャラ名で1人ずつ引いたうえでないと記録してはいけない。**
「紹介で検索して0件だった」は根拠にならない（→ 絶対に守ること 4）。過去にこれで27作品を誤って
「無し」にし、あとで調べ直したら明治東亰恋伽で8人、戦場の円舞曲で7人が見つかった。

依頼者が記入用ブックで「該当なし」とされたものは、判断を尊重してそのまま残す。

## 成果物

依頼されたら次の2つを作って渡す。

- **収集済みXポスト一覧** — 作品別・発売日順に、キャラ名・種別・URL
- **見つからなかったキャラクター一覧** — 「探して無かった（根拠つき）」「アカウント判明・未調査」
  「公式アカウント未特定」の3区分に分ける。キャラはシリーズ内の複数作品に登場するので、
  作品別の表は重複する。実人数も併記する

## ファイルの場所

| パス | 中身 |
|---|---|
| `corrections/x_posts.tsv` | 検証を通った採用ポスト |
| `corrections/x_accounts.tsv` | 作品ごとの公式アカウント。先頭のハンドルがゲーム公式 |
| `corrections/x_none.tsv` | 探して無かった作品・キャラ |
| `corrections/_queue_x.tsv` | intake が書く候補。間違いが混ざってよい |
| `corrections/x_embeds.json` | サイトに渡す埋め込み。embeds が作る |
| `data/` | git管理外。消えたら再生成 |

`x_posts.tsv` から行を消すときは `_queue_x.tsv` からも消す。
`verify` が両方をマージするので、片方だけだと復活する。
