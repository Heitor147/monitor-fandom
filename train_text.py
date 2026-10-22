import argparse

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, hstack
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler

from train_baseline import build_features, load, slog

COUNT_COLS = ["added_chars", "removed_chars", "added_parts", "removed_parts", "old_len", "new_len",
              "cats_added", "cats_removed", "urls_added", "repeats", "excl"]
RATIO_COLS = ["removed_frac", "blanked", "upper_ratio"]


def weighted_topk_recall(y, score, w, frac):
    order = np.argsort(-score)
    cum = np.cumsum(w[order])
    k = int(np.searchsorted(cum, frac * w.sum())) + 1
    sel = order[:k]
    return (w[sel] * y[sel]).sum() / (w * y).sum()


def report(name, y, score, w):
    print(f"{name:34s} PR-AUC={average_precision_score(y, score, sample_weight=w):.3f}  "
          f"recall@top5%={weighted_topk_recall(y, score, w, 0.05):.2f}  "
          f"recall@top10%={weighted_topk_recall(y, score, w, 0.10):.2f}")


def text_numeric(df):
    t = pd.DataFrame(index=df.index)
    for c in COUNT_COLS:
        t[c] = slog(df[c].astype(float))
    for c in RATIO_COLS:
        t[c] = df[c].astype(float)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="data/revisions_*.jsonl")
    ap.add_argument("--text", default="data/text_features.jsonl")
    ap.add_argument("--test-frac", type=float, default=0.3)
    args = ap.parse_args()

    df = load(args.pattern)
    texts = pd.read_json(args.text, lines=True)
    df = df.merge(texts, on=["wiki", "revid"], how="left")  # mantém a ordem por tempo
    X, comment = build_features(df)  # histórico do editor usa TODAS as edições, antes de filtrar
    cut_ts = df["ts"].iloc[int(len(df) * (1 - args.test_frac))]
    has_text = df["added_chars"].notna().values
    tr = has_text & (df["ts"] < cut_ts).values
    te = has_text & (df["ts"] >= cut_ts).values
    y = df["reverted"].values
    w = df["sample_weight"].fillna(1.0).values

    print(f"com texto: treino={tr.sum()} ({y[tr].sum()} revertidas) | teste={te.sum()} ({y[te].sum()} revertidas)")
    print(f"teste vai de {cut_ts.date()} até {df['ts'].iloc[-1].date()}")
    prev = (w[te] * y[te]).sum() / w[te].sum()
    print(f"prevalência real estimada no teste: {prev:.2%} (nível de acaso do PR-AUC)\n")

    TN = text_numeric(df.fillna({c: 0 for c in COUNT_COLS + RATIO_COLS}))
    added = df["added_text"].fillna("")
    removed = df["removed_text"].fillna("")

    # A) só metadados
    sc_a = StandardScaler().fit(X[tr])
    lr_a = LogisticRegression(class_weight="balanced", max_iter=2000, C=0.5)
    lr_a.fit(sc_a.transform(X[tr]), y[tr])
    report("A) metadados (reg. logística)", y[te], lr_a.predict_proba(sc_a.transform(X[te]))[:, 1], w[te])

    # B) metadados + características e palavras do diff
    XN = pd.concat([X, TN], axis=1)
    sc_b = StandardScaler().fit(XN[tr])
    tf_add = TfidfVectorizer(min_df=5, max_features=3000, ngram_range=(1, 2)).fit(added[tr])
    tf_rem = TfidfVectorizer(min_df=5, max_features=1000).fit(removed[tr])
    tf_com = TfidfVectorizer(min_df=5, max_features=1000, ngram_range=(1, 2)).fit(comment[tr])

    def mat(mask):
        return hstack([csr_matrix(sc_b.transform(XN[mask])), tf_add.transform(added[mask]),
                       tf_rem.transform(removed[mask]), tf_com.transform(comment[mask])]).tocsr()

    lr_b = LogisticRegression(class_weight="balanced", max_iter=3000, C=0.3)
    lr_b.fit(mat(tr), y[tr])
    report("B) metadados + texto (reg. log.)", y[te], lr_b.predict_proba(mat(te))[:, 1], w[te])

    # C) gradient boosting em metadados + características numéricas do diff
    wt = np.where(y[tr] == 1, (y[tr] == 0).sum() / max(1, y[tr].sum()), 1.0)
    hgb = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.08, max_iter=200, random_state=0)
    hgb.fit(XN[tr], y[tr], sample_weight=wt)
    report("C) boosting (meta + nº do diff)", y[te], hgb.predict_proba(XN[te])[:, 1], w[te])

    # o que o modelo B aprendeu: confira se faz sentido (e se não tem nada vazando)
    names = (list(XN.columns) + ["add:" + t for t in tf_add.get_feature_names_out()]
             + ["rem:" + t for t in tf_rem.get_feature_names_out()]
             + ["com:" + t for t in tf_com.get_feature_names_out()])
    coef = lr_b.coef_[0]
    top = np.argsort(-coef)[:20]
    print("\nCaracterísticas que mais empurram pra 'vai ser revertida' (modelo B):")
    print(", ".join(f"{names[i]} ({coef[i]:.2f})" for i in top))


if __name__ == "__main__":
    main()
