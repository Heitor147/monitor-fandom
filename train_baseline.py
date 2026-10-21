import argparse
import glob
import json
import re
from collections import Counter

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, hstack
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler

IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$|^[0-9a-fA-F:]+:[0-9a-fA-F:]+$")

def slog(x):
    return np.sign(x) * np.log1p(np.abs(x))

def load(pattern):
    rows = []
    for path in glob.glob(pattern):
        with open(path, encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f)
    df = pd.DataFrame(rows)
    df = df[df["reverted"].notna() & ~df["is_creation"]].copy()
    df["reverted"] = df["reverted"].astype(int)
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("ts").reset_index(drop=True)

def build_features(df):
    f = pd.DataFrame(index=df.index)
    userid = df["userid"].fillna(0).astype(int) if "userid" in df else 0
    f["is_anon"] = ((userid == 0) | df["user"].fillna("").map(lambda u: bool(IP_RE.match(u)))).astype(int)
    f["delta"] = slog(df["delta"])
    f["delta_neg"] = (df["delta"] < 0).astype(int)
    f["delta_ratio"] = slog(df["delta"] / (df["size"] - df["delta"] + 1))
    f["log_size"] = np.log1p(df["size"])
    comment = df["comment"].fillna("")
    f["comment_empty"] = (comment.str.strip() == "").astype(int)
    f["comment_len"] = np.log1p(comment.str.len())
    f["hour"] = df["ts"].dt.hour
    f["weekday"] = df["ts"].dt.weekday
    # histórico só do passado (df já está ordenado por tempo => cumcount não vaza o futuro).
    # Limitação: usuários antigos parecem "novos" no começo da janela coletada.
    g_user = df.groupby(["wiki", "user"])
    f["user_prior"] = slog(g_user.cumcount())
    f["user_age_days"] = slog((df["ts"] - g_user["ts"].transform("min")).dt.days)
    f["page_prior"] = slog(df.groupby(["wiki", "pageid"]).cumcount())
    tags = df["tags"].map(lambda t: t if isinstance(t, list) else [])
    f["n_tags"] = tags.map(len)
    common = [t for t, c in Counter(t for ts_ in tags for t in ts_).items() if c >= 100]
    for t in common:
        f["tag_" + t] = tags.map(lambda ts_, t=t: int(t in ts_))
    f = f.join(pd.get_dummies(df["wiki"], prefix="wiki").astype(int))
    return f, comment

def topk_recall(y, score, frac):
    k = max(1, int(len(y) * frac))
    idx = np.argsort(-score)[:k]
    return y[idx].sum() / max(1, y.sum())

def report(name, y, score):
    print(f"{name:28s} PR-AUC={average_precision_score(y, score):.3f}  "
          f"recall@top5%={topk_recall(y, score, 0.05):.2f}  recall@top10%={topk_recall(y, score, 0.10):.2f}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="data/revisions_*.jsonl")
    ap.add_argument("--test-frac", type=float, default=0.3)
    args = ap.parse_args()

    df = load(args.pattern)
    X, comment = build_features(df)
    y = df["reverted"].values
    cut = int(len(df) * (1 - args.test_frac))
    tr, te = slice(0, cut), slice(cut, None)
    print(f"{len(df)} edições | treino={cut} | teste={len(df) - cut}")
    print(f"revertidas: treino={y[tr].sum()} ({y[tr].mean():.2%}) | teste={y[te].sum()} ({y[te].mean():.2%})")
    print(f"teste vai de {df['ts'].iloc[cut].date()} até {df['ts'].iloc[-1].date()}\n")

    # regra boba de comparação
    rule = X["is_anon"].values * 1.0 + 0.5 * X["delta_neg"].values
    report("regra boba (anônimo+remoção)", y[te], rule[te])

    # modelo 1: gradient boosting nos números
    w = np.where(y[tr] == 1, (y[tr] == 0).sum() / max(1, y[tr].sum()), 1.0)
    hgb = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.08, max_iter=200, random_state=0)
    hgb.fit(X.iloc[tr], y[tr], sample_weight=w)
    report("gradient boosting (números)", y[te], hgb.predict_proba(X.iloc[te])[:, 1])

    # modelo 2: regressão logística em números + TF-IDF do resumo de edição
    scaler = StandardScaler().fit(X.iloc[tr])
    tfidf = TfidfVectorizer(min_df=5, max_features=2000, ngram_range=(1, 2), lowercase=True)
    tfidf.fit(comment.iloc[tr])

    def mat(sl):
        return hstack([csr_matrix(scaler.transform(X.iloc[sl])), tfidf.transform(comment.iloc[sl])]).tocsr()

    lr = LogisticRegression(class_weight="balanced", max_iter=2000, C=0.5)
    lr.fit(mat(tr), y[tr])
    report("regressão logística (+texto)", y[te], lr.predict_proba(mat(te))[:, 1])

    print("\nPrevalência no teste = nível de acaso do PR-AUC. Quanto acima disso, melhor.")


if __name__ == "__main__":
    main()