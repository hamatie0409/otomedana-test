# -*- coding: utf-8 -*-
"""大きな変更の前に取るバックアップ（標準ライブラリのみ）。

    python3 scripts/backup.py "かな検索の作り直し"

data/ は .gitignore されていて git に入らないため、壊すと再生成しか手がない。
そこで「大きな変更」の前にここへ退避する。docs/ の HTML は git 管理下なので
含めず、代わりにその時点のコミットハッシュを控えて git 側から戻せるようにする。

肥大させないための作り:
  data/ の中身は 35MB の DB のように「たまにしか変わらない大物」が大半を占める。
  毎回まるごと固めると 1 回 18MB になるので、大物は内容の SHA1 をファイル名にした
  プール（backups/pool/）に 1 個だけ置き、スナップショットからは参照するだけにする。
  DB を作り直していない限り、2 回目以降のバックアップは実質 0 バイトで済む。

  スナップショット本体 backups/<日時>_<ラベル>.tar.gz には
    - 小さいファイル（scripts/ と data/site/ など）の実体
    - MANIFEST.json（大物のパスとハッシュ、git のコミットなど）
  だけが入る。だいたい 400KB。
"""
import os, sys, json, gzip, shutil, hashlib, tarfile, subprocess, tempfile, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS = os.path.join(ROOT, "backups")
POOL = os.path.join(BACKUPS, "pool")

# 退避する対象。docs/ の HTML は git にあるので入れない。
TARGETS = ["data", "scripts", "docs/_headers", "docs/robots.txt", "docs/sitemap.xml"]
SKIP = {".DS_Store"}
SKIP_DIRS = {"__pycache__"}

# この大きさを超えたらプール送り。小物はスナップショットに直接入れる。
BIG = 1 * 1024 * 1024

# 保持数。環境変数で変えられる。
KEEP = int(os.environ.get("BACKUP_KEEP", "30"))          # スナップショットの本数
KEEP_FULL = int(os.environ.get("BACKUP_KEEP_FULL", "3"))  # 大物まで戻せる世代数
MAX_MB = int(os.environ.get("BACKUP_MAX_MB", "200"))      # backups/ 全体の上限


def run(*args):
    """git の出力を取る。失敗しても落とさない。"""
    try:
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def walk():
    """退避対象のファイルを (絶対パス, ROOT からの相対パス) で列挙する。"""
    for t in TARGETS:
        p = os.path.join(ROOT, t)
        if os.path.isfile(p):
            yield p, t
        elif os.path.isdir(p):
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for name in sorted(filenames):
                    if name in SKIP:
                        continue
                    full = os.path.join(dirpath, name)
                    yield full, os.path.relpath(full, ROOT)


def stash(path):
    """大物をプールへ。同じ内容が既にあれば置かない。追加バイト数を返す。"""
    digest = sha1(path)
    dest = os.path.join(POOL, digest + ".gz")
    if os.path.exists(dest):
        return digest, 0
    os.makedirs(POOL, exist_ok=True)
    tmp = dest + ".tmp"
    with open(path, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as out:
        shutil.copyfileobj(src, out, 1024 * 1024)
    os.replace(tmp, dest)
    return digest, os.path.getsize(dest)


def snapshots():
    """スナップショットを新しい順に返す。"""
    if not os.path.isdir(BACKUPS):
        return []
    names = [n for n in os.listdir(BACKUPS) if n.endswith(".tar.gz")]
    # 名前ではなく更新時刻の新しい順。旧形式の名前が混ざっても順序が崩れない。
    return sorted(names, key=lambda n: os.path.getmtime(os.path.join(BACKUPS, n)),
                  reverse=True)


def manifest_of(name):
    """スナップショットの MANIFEST.json を読む。旧形式なら None。"""
    try:
        with tarfile.open(os.path.join(BACKUPS, name), "r:gz") as tar:
            f = tar.extractfile("MANIFEST.json")
            return json.load(f) if f else None
    except (tarfile.TarError, KeyError, OSError, ValueError):
        return None


def dir_size(path):
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for n in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                pass
    return total


def gc(keep_full=None, protect=()):
    """古いスナップショットを消し、どこからも参照されないプールを捨てる。

    新しい方から keep_full 世代だけが大物まで戻せる。それより古いものは
    スナップショット自体は残るが、大物は復元できない（構造とスクリプトは戻せる）。
    """
    keep_full = KEEP_FULL if keep_full is None else keep_full
    removed = []

    for name in snapshots()[KEEP:]:
        os.remove(os.path.join(BACKUPS, name))
        removed.append(name)

    # 今まさに作ったスナップショットの参照は無条件で守る。
    alive = set(protect)
    kept = 0
    for name in snapshots():
        if kept >= keep_full:
            break
        m = manifest_of(name)
        if not m:
            continue  # 旧形式は世代として数えない（枠を食わせない）
        alive.update(e["sha1"] for e in m.get("pooled", []))
        kept += 1

    freed = 0
    if os.path.isdir(POOL):
        for n in os.listdir(POOL):
            if n.endswith(".gz") and n[:-3] not in alive:
                path = os.path.join(POOL, n)
                freed += os.path.getsize(path)
                os.remove(path)
    return removed, freed


def enforce_cap(protect=()):
    """上限を超えていたら、大物を戻せる世代を減らして削る。

    直近 1 世代は必ず残す。上限より現在のデータ自体が大きい場合は
    それ以上削れないので、警告だけ出して諦める。
    """
    limit = MAX_MB * 1024 * 1024
    full = KEEP_FULL
    while dir_size(BACKUPS) > limit and full > 1:
        full -= 1
        gc(keep_full=full, protect=protect)
    if full < KEEP_FULL:
        print("上限 %dMB のため、大物まで戻せるのは直近 %d 世代に絞りました" % (MAX_MB, full))


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "manual"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = "".join(c for c in label.replace(" ", "_").replace("/", "_")
                   if c.isalnum() or c in "_-ー")[:40] or "manual"
    out = os.path.join(BACKUPS, "%s_%s.tar.gz" % (stamp, slug))
    os.makedirs(BACKUPS, exist_ok=True)

    manifest = {
        "label": label,
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": run("git", "rev-parse", "HEAD"),
        "tracked_dirty": len([l for l in run("git", "status", "--porcelain",
                                             "--untracked-files=no").splitlines() if l]),
        "pooled": [],
    }

    added = 0          # プールに新しく積んだバイト数
    inline_files = []  # スナップショットに直接入れるファイル

    for full, rel in walk():
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if size >= BIG:
            digest, grew = stash(full)
            added += grew
            manifest["pooled"].append({"path": rel, "sha1": digest, "size": size})
        else:
            inline_files.append((full, rel))

    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                      encoding="utf-8")
    json.dump(manifest, tmp, ensure_ascii=False, indent=1)
    tmp.close()
    try:
        with tarfile.open(out, "w:gz") as tar:
            tar.add(tmp.name, arcname="MANIFEST.json")
            for full, rel in inline_files:
                tar.add(full, arcname=rel)
    finally:
        os.unlink(tmp.name)

    # 節目として git タグも打つ。docs/ を当時に戻すときの目印になる。
    tag = "backup/%s" % stamp
    if manifest["tracked_dirty"] == 0 and manifest["commit"]:
        run("git", "tag", "-a", tag, "-m", "backup: %s" % label)
        print("git タグ:  %s   （push は  git push origin %s ）" % (tag, tag))
    else:
        print("git タグ:  未作成（未コミットの変更あり。commit 後に再実行すれば打てます）")

    mine = [e["sha1"] for e in manifest["pooled"]]
    removed, freed = gc(protect=mine)
    enforce_cap(protect=mine)

    snap = os.path.getsize(out)
    pooled_mb = sum(e["size"] for e in manifest["pooled"]) / 1024.0 / 1024.0
    print("スナップショット: %s  (%.0fKB)" % (os.path.relpath(out, ROOT), snap / 1024.0))
    print("大物 %d 件 %.0fMB は共有プール参照 / 今回の追加 %.1fMB"
          % (len(manifest["pooled"]), pooled_mb, added / 1024.0 / 1024.0))
    if removed:
        print("古いスナップショット %d 件を削除" % len(removed))
    if freed:
        print("未参照のプール %.1fMB を回収" % (freed / 1024.0 / 1024.0))
    print("backups/ 合計: %.1fMB（上限 %dMB）" % (dir_size(BACKUPS) / 1024.0 / 1024.0, MAX_MB))


if __name__ == "__main__":
    main()
