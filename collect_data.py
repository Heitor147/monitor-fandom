import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import requests

HEADERS = {"User-Agent": "fandom-watcher-ra/0.1 (coleta de dados pessoal)"}
LOOKAHEAD = 5
SLEEP = 0.6  # educação com a API da Fandom


def fetch_all_revisions(wiki, max_revisions):
    """Baixa revisões via list=allrevisions, com paginação."""
    url = f"https://{wiki}.fandom.com/api.php"
    params = {
        "action": "query",
        "list": "allrevisions",
        "arvlimit": 500,
        "arvdir": "newer",
        "arvprop": "ids|timestamp|user|userid|comment|size|sha1|tags",
        "arvnamespace": 0,
        "format": "json",
        "formatversion": 2,
    }
    out = []
    while len(out) < max_revisions:
        r = requests.get(url, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        for page in data.get("query", {}).get("allrevisions", []):
            for rev in page.get("revisions", []):
                rev["pageid"] = page["pageid"]
                rev["title"] = page["title"]
                out.append(rev)
        if "continue" not in data:
            break
        params.update(data["continue"])
        time.sleep(SLEEP)
    return out[:max_revisions]


def label_revisions(revs):
    """Recebe revisões (dicts) de uma wiki e adiciona 'reverted', 'reverter', 'delta'."""
    by_page = defaultdict(list)
    for rev in revs:
        by_page[rev["pageid"]].append(rev)

    for page_revs in by_page.values():
        page_revs.sort(key=lambda x: x["revid"])
        for i, rev in enumerate(page_revs):
            prev = page_revs[i - 1] if i > 0 else None
            rev["is_creation"] = prev is None
            rev["delta"] = rev.get("size", 0) - (prev.get("size", 0) if prev else 0)
            rev["reverted"] = None
            rev["reverter"] = None
            if prev is None:
                continue
            if "mw-reverted" in rev.get("tags", []):
                rev["reverted"] = True
            parent_sha = prev.get("sha1")
            if not parent_sha or rev.get("sha1hidden") or prev.get("sha1hidden"):
                continue
            if rev["reverted"] is None:
                rev["reverted"] = False
            for later in page_revs[i + 1 : i + 1 + LOOKAHEAD]:
                if later.get("sha1") == parent_sha and later.get("user") != rev.get("user"):
                    rev["reverted"] = True
                    rev["reverter"] = later.get("user")
                    break
    return revs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--wiki", help="só esta wiki (senão todas do config)")
    ap.add_argument("--max", type=int, default=20000, help="máx. de revisões por wiki")
    args = ap.parse_args()

    config = json.load(open(args.config, encoding="utf-8"))
    wikis = [args.wiki] if args.wiki else list(config.get("wikis", {}).keys())
    Path("data").mkdir(exist_ok=True)

    for wiki in wikis:
        print(f"[{wiki}] baixando revisões...")
        try:
            revs = fetch_all_revisions(wiki, args.max)
        except Exception as e:
            print(f"[{wiki}] falhou: {type(e).__name__}: {e}")
            continue
        revs = label_revisions(revs)
        for rev in revs:
            rev["wiki"] = wiki
        path = Path("data") / f"revisions_{wiki}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for rev in revs:
                f.write(json.dumps(rev, ensure_ascii=False) + "\n")
        labeled = [r for r in revs if r["reverted"] is not None]
        pos = sum(1 for r in labeled if r["reverted"])
        print(f"[{wiki}] {len(revs)} revisões, {len(labeled)} rotuláveis, {pos} revertidas")


if __name__ == "__main__":
    main()