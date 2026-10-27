"""Entrena el modelo de instrucciones desde cero.

    python -m payroll_ai.train_instructions            # entrenamiento completo
    python -m payroll_ai.train_instructions --quick    # prueba rápida
"""
from __future__ import annotations

import argparse
import collections
import os
import time

import numpy as np

from .datagen import INTENTS, TAG2ID, TAGS, build_dataset
from .model import (InstructionModel, N_CHAR_BUCKETS, featurize_tagger, intent_ids,
                    intent_vocab_size, pad_ids, word_key)
from .nn import Adam, BagMLP, TaggerNet
from .text import N_SHAPES

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "instructions")


def build_vocab(data, min_freq=3):
    cnt = collections.Counter(word_key(t) for toks, _, _ in data for t in toks)
    words = [w for w, c in cnt.items() if c >= min_freq]
    return {w: i + 2 for i, w in enumerate(sorted(words))}


def evaluate(model: InstructionModel, data):
    """Intención: exactitud. Entidades: F1 por span exacto (tag + límites)."""
    from .model import decode_spans
    from .text import Token

    ok, tp, fp, fn = 0, 0, 0, 0
    per_ent = collections.defaultdict(lambda: [0, 0, 0])
    for toks, tags, intent in data:
        # se evalúa sobre los mismos tokens (aísla el modelo del tokenizador)
        surf = toks
        ids = pad_ids([intent_ids(surf, model.vocab)])
        ok += INTENTS[int(model.intent_net.predict_proba(ids)[0].argmax())] == intent
        w, c, s = featurize_tagger(surf, model.vocab)
        pr = model.tagger.predict_proba(model.tagger.make_batch([(w, c, s)])).argmax(1)
        from .model import _repair_bio
        pred_tags = _repair_bio([TAGS[i] for i in pr])
        tk = [Token(t, t) for t in toks]
        gold = {(sp.tag, sp.start, sp.end) for sp in decode_spans(tk, tags)}
        pred = {(sp.tag, sp.start, sp.end) for sp in decode_spans(tk, pred_tags)}
        tp += len(gold & pred); fp += len(pred - gold); fn += len(gold - pred)
        for g in gold & pred: per_ent[g[0]][0] += 1
        for g in pred - gold: per_ent[g[0]][1] += 1
        for g in gold - pred: per_ent[g[0]][2] += 1
    prec = tp / max(tp + fp, 1); rec = tp / max(tp + fn, 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)
    ent = {k: (2 * a / max(2 * a + b + c, 1)) for k, (a, b, c) in per_ent.items()}
    return {"intent_acc": ok / len(data), "span_f1": f1, "span_prec": prec, "span_rec": rec, "per_entity_f1": ent}


def main(quick=False, seed=0):
    rng = np.random.default_rng(seed)
    n_train, n_test = (3000, 600) if quick else (60000, 4000)
    epochs = 2 if quick else 10
    print(f"Generando datos: {n_train} entrenamiento / {n_test} prueba (entidades distintas)…")
    train = build_dataset(n_train, "train", seed=seed + 1)
    test = build_dataset(n_test, "test", seed=seed + 2)
    vocab = build_vocab(train)
    print(f"Vocabulario: {len(vocab)} palabras")

    # --------- clasificador de intención ---------------------------------------
    Xi = [intent_ids(t, vocab) for t, _, _ in train]
    yi = np.array([INTENTS.index(i) for _, _, i in train])
    net_i = BagMLP(intent_vocab_size(vocab), 64, 0, 128, len(INTENTS), seed=seed)
    opt = Adam(net_i.p, lr=3e-3)
    B = 64
    for ep in range(epochs):
        perm = rng.permutation(len(Xi)); tot = 0; nb = 0
        for b in range(0, len(perm), B):
            idx = perm[b:b + B]
            ids = pad_ids([Xi[i] for i in idx])
            # dropout de ids (robustez a palabras desconocidas)
            ids = np.where(rng.random(ids.shape) < 0.08, 0, ids)
            loss, g = net_i.loss_grads(ids, np.zeros((len(idx), 0), np.float32), yi[idx], rng)
            opt.step(g); tot += loss; nb += 1
        print(f"[intención] época {ep + 1}/{epochs} pérdida {tot / nb:.4f}")

    # --------- etiquetador de entidades ----------------------------------------
    feats = []
    for toks, tags, _ in train:
        feats.append((featurize_tagger(toks, vocab), np.array([TAG2ID[t] for t in tags])))
    net_t = TaggerNet(len(vocab) + 2, N_CHAR_BUCKETS, N_SHAPES, len(TAGS), seed=seed)
    opt = Adam(net_t.p, lr=2e-3)
    # pesos de clase: las entidades pesan más que 'O'
    class_w = np.ones(len(TAGS), np.float32); class_w[1:] = 2.0
    B = 48
    t0 = time.time()
    for ep in range(epochs):
        perm = rng.permutation(len(feats)); tot = 0; nb = 0
        lr = 2e-3 * (0.5 ** (ep // 4))
        for b in range(0, len(perm), B):
            sents, ys = [], []
            for i in perm[b:b + B]:
                (w, c, s), y = feats[i]
                drop = rng.random(len(w)) < np.where(y > 0, 0.25, 0.08)   # palabra -> UNK
                w = np.where(drop, 1, w)
                sents.append((w, c, s)); ys.append(y)
            loss, g = net_t.loss_grads(net_t.make_batch(sents), np.concatenate(ys), rng, class_w)
            opt.step(g, lr=lr); tot += loss; nb += 1
        print(f"[entidades] época {ep + 1}/{epochs} pérdida {tot / nb:.4f}  ({time.time() - t0:.0f}s)")

    model = InstructionModel(vocab, net_i, net_t)
    res_tr = evaluate(model, train[:2000])
    res = evaluate(model, test)
    print("\nEntrenamiento (muestra):", {k: round(v, 4) for k, v in res_tr.items() if not isinstance(v, dict)})
    print("PRUEBA (entidades y plantillas no vistas):", {k: round(v, 4) for k, v in res.items() if not isinstance(v, dict)})
    print("F1 por entidad:", {k: round(v, 3) for k, v in sorted(res["per_entity_f1"].items())})
    model.save(MODEL_DIR)
    print("Guardado en", MODEL_DIR)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    main(a.quick, a.seed)
