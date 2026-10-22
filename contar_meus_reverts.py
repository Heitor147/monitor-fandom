import glob, json, collections
ME = "The One Who Theorizes"
cfg = json.load(open("config.json", encoding="utf-8"))["wikis"]
mine = {w: {p.replace("_", " ") for p in v.get("pages", [])} for w, v in cfg.items()}
s = collections.defaultdict(lambda: [0, 0, 0])
for path in glob.glob("data/revisions_*.jsonl"):
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if r["title"] not in mine.get(r["wiki"], ()) or r["user"] == ME:
            continue
        s[r["wiki"]][0] += 1
        s[r["wiki"]][1] += r.get("reverter") == ME
        s[r["wiki"]][2] += bool(r.get("reverted"))
for w, (n, mine_rev, any_rev) in s.items():
    print(f"{w}: {n} edições de outros nas suas páginas | você reverteu {mine_rev} | revertidas por qualquer um {any_rev}")