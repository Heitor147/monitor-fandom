import argparse
import difflib
import glob
import json
import random
import re
import time
from collections import defaultdict
from pathlib import Path

import requests

HEADERS = {"User-Agent": "fandom-watcher-ra/0.1 (coleta de dados pessoal)"}
SLEEP = 0.6
MAX_TEXT = 1500
CAT_RE = re.compile(r"\[\[\s*(?:category|categoria)\s*:[^\]]*\]\]", re.I)
URL_RE = re.compile(r"https?://[^\s\]|}]+", re.I)
REPEAT_RE = re.compile(r"(.)\1{4,}")


# ---------- diff local ----------

def _word_diff(a_lines, b_lines):
    """Diff por palavras dentro de um bloco alterado (fanon costuma ter parágrafos de uma linha só)."""
    aw, bw = " ".join(a_lines).split(), " ".join(b_lines).split()
    if len(aw) > 4000 or len(bw) > 4000:
        return list(a_lines), list(b_lines)
    sm = difflib.SequenceMatcher(None, aw, bw, autojunk=False)
    rem, add = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            rem.append(" ".join(aw[i1:i2]))
        if tag in ("replace", "insert"):
            add.append(" ".join(bw[j1:j2]))
    return rem, add


def diff_features(old, new):
    a, b = old.splitlines(), new.splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=(len(a) + len(b) > 4000))
    added, removed = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete":
            removed += a[i1:i2]
        elif tag == "insert":
            added += b[j1:j2]
        elif tag == "replace":
            rem, add = _word_diff(a[i1:i2], b[j1:j2])
            removed += rem
            added += add
    add_t, rem_t = "\n".join(added), "\n".join(removed)
    letters = [c for c in add_t if c.isalpha()]
    cats_old, cats_new = set(CAT_RE.findall(old)), set(CAT_RE.findall(new))
    urls_old, urls_new = set(URL_RE.findall(old)), set(URL_RE.findall(new))
    return {
        "added_chars": len(add_t),
        "removed_chars": len(rem_t),
        "added_parts": len(added),
        "removed_parts": len(removed),
        "old_len": len(old),
        "new_len": len(new),
        "removed_frac": len(rem_t) / (len(old) + 1),
        "blanked": int(len(old) > 200 and len(new) < 0.1 * len(old)),
        "cats_added": len(cats_new - cats_old),
        "cats_removed": len(cats_old - cats_new),
        "urls_added": len(urls_new - urls_old),
        "upper_ratio": (sum(c.isupper() for c in letters) / len(letters)) if letters else 0.0,
        "repeats": len(REPEAT_RE.findall(add_t)),
        "excl": add_t.count("!"),
        "added_text": add_t[:MAX_TEXT],
        "removed_text": rem_t[:MAX_TEXT],
    }


# ---------- API ----------

def fetch_texts(wiki, revids):
    params = {
        "action": "query",
        "prop": "revisions",
        "revids": "|".join(str(r) for r in revids),
        "rvprop": "ids|content",
        "rvslots": "main",
        "format": "json",
        "formatversion": 2,
    }
    data = None
    for attempt in range(3):
        try:
            r = requests.get(f"https://{wiki}.fandom.com/api.php", params=params, headers=HEADERS, timeout=60)
            r.raise_for_status()
            data = r.json()
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    texts = {}
    for page in data.get("query", {}).get("pages", []):
        for rev in page.get("revisions", []):
            slot = (rev.get("slots") or {}).get("main") or {}
            txt = slot.get("content", rev.get("content", rev.get("*")))
            if txt is not None:
                texts[rev["revid"]] = txt
    return texts


# ---------- amostragem ----------

def load_meta(pattern):
    rows = []
    for path in glob.glob(pattern):
        with open(path, encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f)
    return [r for r in rows if r.get("reverted") is not None and not r.get("is_creation") and r.get("parentid")]


def sample(rows, neg_ratio, seed):
    rng = random.Random(seed)
    by_wiki = defaultdict(lambda: ([], []))
    for r in rows:
        by_wiki[r["wiki"]][0 if r["reverted"] else 1].append(r)
    out = []
    for wiki, (pos, neg) in by_wiki.items():
        n_neg = min(len(neg), neg_ratio * len(pos)) if pos else 0
        picked = rng.sample(neg, n_neg) if n_neg else []
        w_neg = (len(neg) / n_neg) if n_neg else 1.0
        out += [(r, 1.0) for r in pos] + [(r, w_neg) for r in picked]
        print(f"[{wiki}] {len(pos)} revertidas + {n_neg} normais amostradas (peso {w_neg:.1f})")
    rng.shuffle(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="data/revisions_*.jsonl")
    ap.add_argument("--out", default="data/text_features.jsonl")
    ap.add_argument("--neg-ratio", type=int, default=8)
    ap.add_argument("--batch", type=int, default=24, help="revids por chamada (edição + pai)")
    ap.add_argument("--limit", type=int, help="processar só N edições (teste)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    out_path = Path(args.out)
    done = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                done.add((d["wiki"], d["revid"]))

    samples = [(r, w) for r, w in sample(load_meta(args.pattern), args.neg_ratio, args.seed)
               if (r["wiki"], r["revid"]) not in done]
    if args.limit:
        samples = samples[: args.limit]
    print(f"{len(samples)} edições pra baixar ({len(done)} já feitas)")

    by_wiki = defaultdict(list)
    for r, w in samples:
        by_wiki[r["wiki"]].append((r, w))

    per_call = max(1, args.batch // 2)
    n_ok = n_skip = 0
    with open(out_path, "a", encoding="utf-8") as f:
        for wiki, items in by_wiki.items():
            for i in range(0, len(items), per_call):
                chunk = items[i : i + per_call]
                ids = sorted({x for r, _ in chunk for x in (r["revid"], r["parentid"])})
                try:
                    texts = fetch_texts(wiki, ids)
                except Exception as e:
                    print(f"[{wiki}] lote falhou ({type(e).__name__}): {e}")
                    n_skip += len(chunk)
                    continue
                for r, w in chunk:
                    old, new = texts.get(r["parentid"]), texts.get(r["revid"])
                    if old is None or new is None:
                        n_skip += 1
                        continue
                    rec = {"wiki": wiki, "revid": r["revid"], "sample_weight": w}
                    rec.update(diff_features(old, new))
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_ok += 1
                f.flush()
                if (i // per_call) % 20 == 0:
                    print(f"[{wiki}] {n_ok} ok, {n_skip} puladas")
                time.sleep(SLEEP)
    print(f"Pronto: {n_ok} gravadas, {n_skip} puladas (texto indisponível ou lote com erro).")


if __name__ == "__main__":
    main()
