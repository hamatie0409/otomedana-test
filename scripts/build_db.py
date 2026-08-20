"""games.jsonl を SQLite (data/otomegame.db) に流し込む。何度実行しても同じ結果になる。

  python3 scripts/build_db.py
"""
import os, json, sqlite3
from common import DATA

SCHEMA = """
DROP TABLE IF EXISTS games;
DROP TABLE IF EXISTS characters;
DROP TABLE IF EXISTS tags;

CREATE TABLE games (
    id INTEGER PRIMARY KEY, url TEXT, title TEXT, game_type TEXT,
    platform TEXT, series TEXT, series_id INTEGER,
    release_date TEXT, release_date_iso TEXT,
    genre TEXT, genre_sub TEXT, price TEXT, official_site TEXT, official_blog TEXT,
    maker TEXT, maker_id INTEGER, maker_site TEXT,
    character_design TEXT, scenario TEXT, image_url TEXT, asin TEXT,
    bonus TEXT, last_updated TEXT
);
CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(id),
    section TEXT, name TEXT, cv TEXT
);
CREATE TABLE tags (
    game_id INTEGER REFERENCES games(id), keyword TEXT
);
CREATE INDEX idx_char_game ON characters(game_id);
CREATE INDEX idx_char_cv   ON characters(cv);
CREATE INDEX idx_tag_game  ON tags(game_id);
CREATE INDEX idx_game_maker ON games(maker);
CREATE INDEX idx_game_series ON games(series);
CREATE INDEX idx_game_date ON games(release_date_iso);
CREATE INDEX idx_char_name ON characters(name);
"""

COLS = ["id", "url", "title", "game_type", "platform", "series", "series_id",
        "release_date", "release_date_iso", "genre", "genre_sub",
        "price", "official_site", "official_blog",
        "maker", "maker_id", "maker_site", "character_design", "scenario",
        "image_url", "asin", "bonus", "last_updated"]

def main():
    db_path = os.path.join(DATA, "otomegame.db")
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    ng = nc = nt = 0
    with open(os.path.join(DATA, "games.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            con.execute("INSERT INTO games VALUES (%s)" % ",".join("?" * len(COLS)),
                        [r.get(c) for c in COLS])
            ng += 1
            for c in r.get("characters", []):
                con.execute("INSERT INTO characters (game_id, section, name, cv) VALUES (?,?,?,?)",
                            (r["id"], c.get("section"), c.get("character"), c.get("cv")))
                nc += 1
            for k in r.get("keywords", []):
                con.execute("INSERT INTO tags VALUES (?,?)", (r["id"], k))
                nt += 1
    con.commit()
    con.close()
    print("games=%d characters=%d tags=%d -> %s" % (ng, nc, nt, db_path))

if __name__ == "__main__":
    main()
