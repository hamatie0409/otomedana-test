# -*- coding: utf-8 -*-
"""バックアップの一覧表示と復元。

    python3 scripts/restore.py                  一覧を見る
    python3 scripts/restore.py 20260820_144006  そのバックアップに戻す

data/ と scripts/ を上書きするが、上書きの前に現状を自動で退避するので
戻しすぎてもやり直せる。docs/ は git 管理下なので、当時のサイトに戻したいときは
一覧に出るコミットハッシュを git checkout する。
"""
import os, sys, gzip, json, shutil, tarfile, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS = os.path.join(ROOT, "backups")
POOL = os.path.join(BACKUPS, "pool")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backup as backup_mod


def show():
    names = backup_mod.snapshots()
    if not names:
        print("バックアップはまだありません。")
        print("  python3 scripts/backup.py \"やること\"")
        return
    print("バックアップ一覧（新しい順）\n")
    for name in names:
        m = backup_mod.manifest_of(name)
        size = os.path.getsize(os.path.join(BACKUPS, name)) / 1024.0
        stamp = name.split("_")[0] + "_" + name.split("_")[1] if "_" in name else name
        if not m:
            print("  %s  (%.0fKB)  … 旧形式のバックアップ" % (name, size))
            print()
            continue
        have = sum(1 for e in m["pooled"]
                   if os.path.exists(os.path.join(POOL, e["sha1"] + ".gz")))
        state = ("大物 %d/%d 件あり" % (have, len(m["pooled"]))
                 if have == len(m["pooled"])
                 else "大物は保持期間外（構造とスクリプトのみ復元可）")
        print("  %s" % stamp)
        print("    %s" % m["label"])
        print("    %s / %s / commit %s" % (m["date"], m["branch"], (m["commit"] or "")[:8]))
        print("    %.0fKB + %s" % (size, state))
        print()
    example = next(("_".join(n.split("_")[:2]) for n in names if backup_mod.manifest_of(n)),
                   "<日時>")
    print("復元:  python3 scripts/restore.py <日時>   例) python3 scripts/restore.py %s"
          % example)


def restore(stamp):
    matches = [n for n in backup_mod.snapshots() if stamp in n]
    if not matches:
        sys.exit("見つかりません: %s" % stamp)
    name = matches[0]
    path = os.path.join(BACKUPS, name)
    m = backup_mod.manifest_of(name)
    if not m:
        sys.exit("旧形式のバックアップです。手動で展開してください: tar -xzf %s" % path)

    missing = [e for e in m["pooled"]
               if not os.path.exists(os.path.join(POOL, e["sha1"] + ".gz"))]
    print("復元元: %s" % name)
    print("  %s / %s / commit %s" % (m["label"], m["date"], (m["commit"] or "")[:8]))
    if missing:
        print("  注意: 大物 %d 件は保持期間外で復元できません:" % len(missing))
        for e in missing:
            print("        %s" % e["path"])
        print("        → 復元後に再生成してください（vndb_build.py など）")
    ans = input("\ndata/ と scripts/ を上書きします。よろしいですか? [y/N] ").strip().lower()
    if ans != "y":
        sys.exit("中止しました")

    # 念のため今の状態を退避してから上書きする
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "backup.py"),
                    "restore前の自動退避"], cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name == "MANIFEST.json" or not member.isfile():
                continue
            # 展開先がリポジトリの外に出るものは無視する（Python 3.9 に
            # tarfile の filter= がないため自前で見る）
            if member.name.startswith("/") or ".." in member.name.split("/"):
                continue
            tar.extract(member, ROOT)

    for e in m["pooled"]:
        src = os.path.join(POOL, e["sha1"] + ".gz")
        if not os.path.exists(src):
            continue
        dest = os.path.join(ROOT, e["path"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with gzip.open(src, "rb") as f, open(dest + ".tmp", "wb") as out:
            shutil.copyfileobj(f, out, 1024 * 1024)
        os.replace(dest + ".tmp", dest)

    print("\n復元しました。")
    if m["commit"]:
        print("サイト（docs/）も当時に戻すなら:  git checkout %s" % m["commit"])


if __name__ == "__main__":
    if len(sys.argv) > 1:
        restore(sys.argv[1])
    else:
        show()
