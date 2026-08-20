"""VNDBダンプ（vndb/db/*）から日本語の乙女ゲームを抽出し data/vndb_games.jsonl を作る。

  python3 scripts/vndb_build.py

ダンプはPostgreSQLのCOPY形式（エスケープ付きTSV）。ネットワークアクセスなし。
"""
import os, sys, json, urllib.parse
from collections import defaultdict
from common import DATA, ROOT
from ja_labels import TAG_JA, TRAIT_JA, TRAIT_GROUP_JA

DB = os.path.join(ROOT, "vndb", "db")
OTOME_TAG = "g542"

PLATFORM_JA = {
    "win": "Windows", "mac": "Mac", "lin": "Linux", "and": "Android", "ios": "iOS",
    "web": "ブラウザ", "dvd": "DVDプレイヤー", "mob": "携帯電話",
    "swi": "Nintendo Switch", "sw2": "Nintendo Switch 2",
    "psv": "PS Vita", "psp": "PSP", "ps1": "PS", "ps2": "PS2", "ps3": "PS3",
    "ps4": "PS4", "ps5": "PS5",
    "nds": "ニンテンドーDS", "n3d": "ニンテンドー3DS", "gba": "ゲームボーイアドバンス",
    "gbc": "ゲームボーイカラー", "nes": "ファミコン", "sfc": "スーパーファミコン",
    "sat": "セガサターン", "drc": "ドリームキャスト", "pce": "PCエンジン",
    "xbo": "Xbox One", "xxs": "Xbox Series X|S", "xb3": "Xbox 360",
    "p88": "PC-88", "p98": "PC-98", "x68": "X68000", "fmt": "FM TOWNS",
    "fm7": "FM-7", "msx": "MSX", "dos": "DOS", "x1s": "X1", "bdp": "Blu-rayプレイヤー",
    "vnd": "その他", "oth": "その他",
}


LANG_JA = {
    "ja": "日本語", "en": "英語", "zh-Hans": "中国語(簡体)", "zh-Hant": "中国語(繁体)",
    "zh": "中国語", "ko": "韓国語", "ru": "ロシア語", "es": "スペイン語",
    "fr": "フランス語", "de": "ドイツ語", "pt-br": "ポルトガル語(ブラジル)",
    "pt-pt": "ポルトガル語", "it": "イタリア語", "pl": "ポーランド語",
    "tr": "トルコ語", "id": "インドネシア語", "vi": "ベトナム語", "th": "タイ語",
    "ar": "アラビア語", "nl": "オランダ語", "sv": "スウェーデン語", "uk": "ウクライナ語",
    "cs": "チェコ語", "hu": "ハンガリー語", "fi": "フィンランド語", "da": "デンマーク語",
    "no": "ノルウェー語", "ro": "ルーマニア語", "el": "ギリシャ語", "he": "ヘブライ語",
    "hi": "ヒンディー語", "ca": "カタルーニャ語", "eo": "エスペラント", "la": "ラテン語",
    "sk": "スロバキア語", "sl": "スロベニア語", "sr": "セルビア語", "hr": "クロアチア語",
    "bg": "ブルガリア語", "lt": "リトアニア語", "lv": "ラトビア語", "et": "エストニア語",
    "fa": "ペルシア語", "ms": "マレー語", "tl": "タガログ語", "mk": "マケドニア語",
    "be": "ベラルーシ語", "eu": "バスク語", "ga": "アイルランド語", "gl": "ガリシア語",
    "is": "アイスランド語", "sq": "アルバニア語", "ta": "タミル語", "ur": "ウルドゥー語",
    "af": "アフリカーンス語", "ka": "ジョージア語", "bn": "ベンガル語",
    "mn": "モンゴル語", "ne": "ネパール語", "sw": "スワヒリ語",
}


LENGTH_JA = {"1": "とても短い (〜2時間)", "2": "短い (2〜10時間)", "3": "普通 (10〜30時間)",
             "4": "長い (30〜50時間)", "5": "とても長い (50時間〜)"}
RELATION_JA = {"seq": "続編", "preq": "前作", "set": "同一設定", "alt": "別バージョン",
               "char": "キャラ共通", "side": "サイドストーリー", "par": "本編",
               "ser": "同シリーズ", "fan": "ファン作品", "orig": "原作"}
STAFF_ROLE_JA = {"scenario": "シナリオ", "art": "原画", "chardesign": "キャラクターデザイン",
                 "music": "音楽", "songs": "主題歌", "director": "監督",
                 "translator": "翻訳", "editor": "編集", "qa": "QA", "staff": "スタッフ"}


def img_url(img):
    """vn.image ("cv20339") を実際の画像URLに変換する"""
    if not img or len(img) < 3:
        return None
    kind, num = img[:2], img[2:]
    return "https://t.vndb.org/%s/%s/%s.jpg" % (kind, num[-2:].zfill(2), num)


def vndb_date(d):
    """VNDBの日付(YYYYMMDD, 不明部分は99)を ISO風の文字列にする"""
    if not d or len(d) != 8 or not d.isdigit():
        return None
    y, m, dd = d[:4], d[4:6], d[6:]
    if y == "9999":
        return None
    if m == "99":
        return y
    if dd == "99":
        return "%s-%s" % (y, m)
    return "%s-%s-%s" % (y, m, dd)


def unesc(v):
    if v == "\\N":
        return None
    if "\\" in v:
        v = (v.replace("\\t", "\t").replace("\\n", "\n")
              .replace("\\r", "\r").replace("\\\\", "\\"))
    return v


def read(table):
    """ダンプの1テーブルを dict の列として順に返す"""
    path = os.path.join(DB, table)
    cols = open(path + ".header", encoding="utf-8").read().rstrip("\n").split("\t")
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield dict(zip(cols, (unesc(v) for v in line.rstrip("\n").split("\t"))))


def otome_vids():
    """乙女ゲームタグの投票を集計し、平均が正のVNだけ残す"""
    tot, cnt = defaultdict(float), defaultdict(int)
    for r in read("tags_vn"):
        if r["tag"] == OTOME_TAG and r["ignore"] != "t":
            tot[r["vid"]] += float(r["vote"])
            cnt[r["vid"]] += 1
    return {v for v in tot if tot[v] / cnt[v] > 0}


def main():
    print("乙女ゲームタグを集計中...", flush=True)
    otome = otome_vids()
    print("  タグ該当: %d件" % len(otome))

    # --- VN本体（原語が日本語のものに絞る） ---
    vns = {}
    for r in read("vn"):
        if r["id"] in otome and r["olang"] == "ja":
            vns[r["id"]] = {
                "vid": r["id"], "olang": r["olang"],
                "devstatus": {"0": "完成", "1": "開発中", "2": "開発中止"}.get(r["devstatus"]),
                "rating": float(r["c_rating"]) / 100 if r["c_rating"] else None,
                "votecount": int(r["c_votecount"]) if r["c_votecount"] else 0,
                "description": r["description"],
                "image_url": img_url(r["image"]),
                "length": LENGTH_JA.get(r["length"]),
                "length_minutes": (int(r["c_length"])
                                   if r["c_length"] and r["c_length"] != "0" else None),
            }
    print("  日本語の乙女ゲーム: %d件" % len(vns))

    # --- タイトル（日本語原文＋ローマ字） ---
    for r in read("vn_titles"):
        v = vns.get(r["id"])
        if v and r["lang"] == "ja":
            v["title"] = r["title"]
            v["title_latin"] = r["latin"]
    for r in read("vn_titles"):
        v = vns.get(r["id"])
        if v and not v.get("title"):
            v["title"] = r["title"]
            v["title_latin"] = r["latin"]

    # --- リリース → 機種・発売日・メーカー ---
    rel2vn = defaultdict(list)
    for r in read("releases_vn"):
        if r["vid"] in vns:
            rel2vn[r["id"]].append(r["vid"])

    VOICED = {"1": "ボイスなし", "2": "一部シーンのみ", "3": "部分ボイス", "4": "フルボイス"}
    voiced_rank = {}
    relinfo = {}
    for r in read("releases"):
        if r["id"] in rel2vn:
            relinfo[r["id"]] = (r["official"] == "t", r["patch"] == "t")
            for vid in rel2vn[r["id"]]:
                if r["minage"]:
                    n = int(r["minage"])
                    if n > (vns[vid].get("minage") or -1):
                        vns[vid]["minage"] = n
                if r["has_ero"] == "t":
                    vns[vid]["has_ero"] = True
            for vid in rel2vn[r["id"]]:
                if r["voiced"]:
                    n = int(r["voiced"])
                    if n > voiced_rank.get(vid, 0):
                        voiced_rank[vid] = n
                        vns[vid]["voiced"] = VOICED[r["voiced"]]
                d = r["released"]
                if d and d[:4].isdigit():
                    cur = vns[vid].get("released")
                    if cur is None or d < cur:
                        vns[vid]["released"] = d

    for r in read("releases"):
        if r["id"] in rel2vn:
            d = r["released"]
            if d and d[:4].isdigit():
                for vid in rel2vn[r["id"]]:
                    cur = vns[vid].get("released")
                    if cur is None or d < cur:
                        vns[vid]["released"] = d

    for r in read("releases_platforms"):
        for vid in rel2vn.get(r["id"], ()):
            vns[vid].setdefault("_plat", set()).add(r["platform"])

    langinfo = defaultdict(lambda: defaultdict(lambda: {"official": False, "mtl": True}))
    for r in read("releases_titles"):
        if r["id"] not in rel2vn:
            continue
        official, patch = relinfo.get(r["id"], (False, False))
        for vid in rel2vn[r["id"]]:
            e = langinfo[vid][r["lang"]]
            if official and not patch:
                e["official"] = True
            if r["mtl"] != "t":
                e["mtl"] = False

    prod_name = {}
    for r in read("producers"):
        prod_name[r["id"]] = (r["name"], r["latin"])
    for r in read("releases_producers"):
        for vid in rel2vn.get(r["id"], ()):
            nm = prod_name.get(r["pid"], (None, None))[0]
            if not nm:
                continue
            if r["developer"] == "t":
                vns[vid].setdefault("_dev", set()).add(nm)
            if r["publisher"] == "t":
                vns[vid].setdefault("_pub", set()).add(nm)

    # --- キャラクター ---
    cname = {}
    for r in read("chars_names"):
        if r["lang"] == "ja" or r["id"] not in cname:
            cname[r["id"]] = (r["name"], r["latin"])

    csex = {}
    for r in read("chars"):
        csex[r["id"]] = r["sex"]

    roles = defaultdict(dict)   # vid -> cid -> role
    for r in read("chars_vns"):
        if r["vid"] in vns:
            roles[r["vid"]][r["id"]] = r["role"]

    # --- 声優（vn_seiyuu: vid, cid, aid） ---
    alias = {}
    alias_sid = {}
    for r in read("staff_alias"):
        alias[r["aid"]] = (r["name"], r["latin"])
        alias_sid[r["aid"]] = r["id"]        # 名義 → 人物ID

    cv = defaultdict(dict)      # vid -> cid -> (name, latin)
    for r in read("vn_seiyuu"):
        if r["id"] in vns:
            cv[r["id"]][r["cid"]] = alias.get(r["aid"], (None, None))

    # --- ① キャラクター属性（ネタバレなしのみ） ---
    tr_name, tr_gid = {}, {}
    for r in read("traits"):
        tr_name[r["id"]] = r["name"]
        tr_gid[r["id"]] = r["gid"]
    all_cids = {c for d in roles.values() for c in d}
    ctraits = defaultdict(list)
    for r in read("chars_traits"):
        if r["id"] in all_cids and r["spoil"] == "0" and r["lie"] != "t":
            g = tr_name.get(tr_gid.get(r["tid"]))
            n = tr_name.get(r["tid"])
            if not n:
                continue
            key = "%s > %s" % (g, n)
            ctraits[r["id"]].append((TRAIT_GROUP_JA.get(g, g), TRAIT_JA.get(key, n)))

    # --- ② タグ（ネタバレなしのみ・カテゴリ別） ---
    tag_name, tag_cat = {}, {}
    for r in read("tags"):
        tag_name[r["id"]] = r["name"]
        tag_cat[r["id"]] = r["cat"]
    tsum = defaultdict(lambda: defaultdict(lambda: [0.0, 0, 0.0, 0]))
    for r in read("tags_vn"):
        if r["vid"] in vns and r["ignore"] != "t":
            e = tsum[r["vid"]][r["tag"]]
            e[0] += float(r["vote"]); e[1] += 1
            if r["spoiler"] is not None:
                e[2] += float(r["spoiler"]); e[3] += 1
    vtags = {}
    for vid, d in tsum.items():
        keep = []
        for tid, (vs, vn_, sp, sn) in d.items():
            if vs / vn_ <= 0:
                continue
            if sn and sp / sn >= 1.0:      # ネタバレ寄りのタグは除外
                continue
            keep.append((tag_cat.get(tid), TAG_JA.get(tag_name.get(tid), tag_name.get(tid)), vs / vn_))
        keep.sort(key=lambda x: -x[2])
        vtags[vid] = keep

    # --- ③ 関連作品 ---
    relmap = defaultdict(list)
    for r in read("vn_relations"):
        if r["id"] in vns:
            relmap[r["id"]].append((r["vid"], RELATION_JA.get(r["relation"], r["relation"]),
                                    r["official"] == "t"))

    # --- ⑦ スタッフ ---
    vstaff = defaultdict(lambda: defaultdict(list))
    credits = defaultdict(list)
    for r in read("vn_staff"):
        if r["id"] not in vns or r["role"] == "staff":
            continue          # "staff" は総称なので個別ページの対象にしない
        nm, latin = alias.get(r["aid"], (None, None))
        sid = alias_sid.get(r["aid"])
        if not nm or not sid:
            continue
        role = STAFF_ROLE_JA.get(r["role"], r["role"])
        if nm not in vstaff[r["id"]][role]:
            vstaff[r["id"]][role].append(nm)
        key = (sid, role)
        if key not in {(c["sid"], c["role"]) for c in credits[r["id"]]}:
            credits[r["id"]].append(
                {"sid": sid, "role": role, "name": nm, "latin": latin})

    # --- ⑩ 日本語版Wikipedia ---
    ext = {}
    for r in read("extlinks"):
        ext[r["id"]] = (r["site"], r["value"])
    wdq = {}
    for r in read("vn_extlinks"):
        if r["id"] in vns:
            site, val = ext.get(r["link"], (None, None))
            if site == "wikidata":
                wdq[r["id"]] = val
    jawiki = {}
    for r in read("wikidata"):
        jawiki[r["id"]] = r["jawiki"]
    vjawiki = {vid: jawiki.get(q) for vid, q in wdq.items() if jawiki.get(q)}

    ROLE_JA = {"main": "主人公", "primary": "メインキャラ", "side": "サブキャラ", "appears": "登場のみ"}
    ORDER = {"main": 0, "primary": 1, "side": 2, "appears": 3}

    for vid, v in vns.items():
        chars = []
        for cid, role in sorted(roles.get(vid, {}).items(), key=lambda kv: ORDER.get(kv[1], 9)):
            nm, lt = cname.get(cid, (None, None))
            cvn, cvl = cv.get(vid, {}).get(cid, (None, None))
            tg = ctraits.get(cid, [])
            chars.append({
                "cid": cid, "character": nm, "character_latin": lt,
                "role": ROLE_JA.get(role, role), "sex": csex.get(cid),
                "cv": cvn, "cv_latin": cvl,
                "traits": ["%s:%s" % (g, n) for g, n in tg],
                "personality": [n for g, n in tg if g == "性格"],
                "appearance": [n for g, n in tg if g in ("髪", "瞳", "外見")],
                "char_role": [n for g, n in tg if g == "役柄"],
            })
        v["characters"] = chars
        v["platforms"] = sorted(PLATFORM_JA.get(p, p) for p in v.pop("_plat", set()))
        v["developers"] = sorted(v.pop("_dev", set()))
        v["publishers"] = sorted(v.pop("_pub", set()))
        langs = []
        for code, e in langinfo.get(vid, {}).items():
            name = LANG_JA.get(code, code)
            if not e["official"]:
                name += "(ファン翻訳)"
            elif e["mtl"]:
                name += "(機械翻訳)"
            langs.append((code != "ja", name))
        v["languages"] = [n for _, n in sorted(langs)]
        v["languages_official"] = [
            LANG_JA.get(c, c) for c, e in sorted(langinfo.get(vid, {}).items(),
                                                 key=lambda kv: kv[0] != "ja")
            if e["official"]]
        v["lang_count"] = len(langinfo.get(vid, {}))
        tg = vtags.get(vid, [])
        v["tags"] = [n for c, n, _ in tg if c == "cont"]
        v["tags_tech"] = [n for c, n, _ in tg if c == "tech"]
        v["tags_ero"] = [n for c, n, _ in tg if c == "ero"]
        v["relations"] = [{"vid": rv, "type": rt, "official": ro}
                          for rv, rt, ro in relmap.get(vid, [])]
        v["staff"] = {k: vs for k, vs in vstaff.get(vid, {}).items() if k != "スタッフ"}
        v["staff_credits"] = credits.get(vid, [])
        v["jawiki"] = vjawiki.get(vid)
        v["jawiki_url"] = ("https://ja.wikipedia.org/wiki/" +
                           urllib.parse.quote(vjawiki[vid].replace(" ", "_"))) if vjawiki.get(vid) else None
        v["minage"] = v.get("minage")
        v["has_ero"] = bool(v.get("has_ero"))
        v["voiced"] = v.get("voiced")
        if any(c["cv"] for c in chars):
            v["cv_status"] = "CVあり"
        elif v["voiced"] == "ボイスなし":
            v["cv_status"] = "ボイスなしの作品"
        elif v["voiced"] in ("部分ボイス", "フルボイス", "一部シーンのみ"):
            v["cv_status"] = "ボイスありだがCV未入力"
        else:
            v["cv_status"] = "ボイス情報なし"
        v["released"] = vndb_date(v.get("released"))
        v["url"] = "https://vndb.org/" + vid

    out = os.path.join(DATA, "vndb_games.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for v in sorted(vns.values(), key=lambda x: (x.get("released") or "9999", x["vid"])):
            f.write(json.dumps(v, ensure_ascii=False) + "\n")

    withcv = sum(1 for v in vns.values() if any(c["cv"] for c in v["characters"]))
    pairs = sum(1 for v in vns.values() for c in v["characters"] if c["cv"])
    print("作品=%d / うち声優情報あり=%d / キャラ↔声優ペア=%d -> %s"
          % (len(vns), withcv, pairs, out))


if __name__ == "__main__":
    main()
