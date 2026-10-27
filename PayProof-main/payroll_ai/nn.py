"""Redes neuronales escritas desde cero con numpy (forward, backprop y Adam a mano).

No se usa PyTorch/TensorFlow ni ningún modelo preentrenado: todos los pesos nacen
de una inicialización aleatoria y se aprenden con los datos de `datagen.py`.
"""
from __future__ import annotations

import json

import numpy as np

F = np.float32


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


def he(rng, fan_in, fan_out):
    return (rng.standard_normal((fan_in, fan_out)) * np.sqrt(2.0 / fan_in)).astype(F)


class Adam:
    def __init__(self, params: dict, lr=2e-3, b1=0.9, b2=0.999, eps=1e-8, clip=5.0):
        self.params, self.lr, self.b1, self.b2, self.eps, self.clip = params, lr, b1, b2, eps, clip
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}
        self.t = 0

    def step(self, grads: dict, lr=None):
        self.t += 1
        lr = lr or self.lr
        # recorte global de gradiente (estabilidad)
        gnorm = np.sqrt(sum(float((g.astype(np.float64) ** 2).sum()) for g in grads.values()))
        scale = min(1.0, self.clip / (gnorm + 1e-9))
        for k, g in grads.items():
            g = g * scale
            self.m[k] = self.b1 * self.m[k] + (1 - self.b1) * g
            self.v[k] = self.b2 * self.v[k] + (1 - self.b2) * g * g
            mh = self.m[k] / (1 - self.b1 ** self.t)
            vh = self.v[k] / (1 - self.b2 ** self.t)
            self.params[k] -= (lr * mh / (np.sqrt(vh) + self.eps)).astype(F)


# --------------------------------------------------------------------------- #
# Bolsa de ids -> embedding promedio -> MLP   (intención y mapeo de columnas)
# --------------------------------------------------------------------------- #


class BagMLP:
    def __init__(self, vocab: int, emb: int, n_dense: int, hidden: int, n_out: int, seed=0):
        rng = np.random.default_rng(seed)
        self.p = {
            "E": (rng.standard_normal((vocab, emb)) * 0.1).astype(F),
            "W1": he(rng, emb + n_dense, hidden),
            "b1": np.zeros(hidden, F),
            "W2": he(rng, hidden, n_out) * F(0.5),
            "b2": np.zeros(n_out, F),
        }
        self.p["E"][0] = 0  # id 0 = relleno
        self.cfg = dict(vocab=vocab, emb=emb, n_dense=n_dense, hidden=hidden, n_out=n_out)

    def _forward(self, ids, dense, train, rng=None, drop=0.2):
        mask = (ids > 0).astype(F)                              # [B,L]
        cnt = np.maximum(mask.sum(1, keepdims=True), 1)         # [B,1]
        e = (self.p["E"][ids] * mask[..., None]).sum(1) / cnt   # [B,d]
        x = np.concatenate([e, dense], 1) if dense is not None and dense.shape[1] else e
        z1 = x @ self.p["W1"] + self.p["b1"]
        h = np.maximum(z1, 0)
        dmask = None
        if train:
            dmask = (rng.random(h.shape) > drop).astype(F) / F(1 - drop)
            h = h * dmask
        logits = h @ self.p["W2"] + self.p["b2"]
        return logits, (ids, mask, cnt, x, z1, h, dmask)

    def predict_proba(self, ids, dense=None):
        logits, _ = self._forward(ids, dense, False)
        return softmax(logits)

    def loss_grads(self, ids, dense, y, rng, weights=None):
        logits, (ids, mask, cnt, x, z1, h, dmask) = self._forward(ids, dense, True, rng)
        p = softmax(logits)
        B = len(y)
        loss = float(-np.log(p[np.arange(B), y] + 1e-9).mean())
        d = p.copy()
        d[np.arange(B), y] -= 1
        d /= B
        g = {"W2": h.T @ d, "b2": d.sum(0)}
        dh = (d @ self.p["W2"].T) * dmask * (z1 > 0)
        g["W1"] = x.T @ dh
        g["b1"] = dh.sum(0)
        dx = dh @ self.p["W1"].T
        de = dx[:, : self.cfg["emb"]] / cnt                     # [B,d]
        gE = np.zeros_like(self.p["E"])
        np.add.at(gE, ids.reshape(-1), (de[:, None, :] * mask[..., None]).reshape(-1, de.shape[1]))
        gE[0] = 0
        g["E"] = gE
        return loss, g

    def state(self):
        return {**{f"p_{k}": v for k, v in self.p.items()}, "cfg": np.array(json.dumps(self.cfg))}

    @classmethod
    def from_state(cls, st):
        cfg = json.loads(str(st["cfg"]))
        m = cls(**cfg)
        for k in m.p:
            m.p[k] = st[f"p_{k}"].astype(F)
        return m


# --------------------------------------------------------------------------- #
# Etiquetador de secuencias: ventana de contexto + MLP   (extracción de entidades)
# --------------------------------------------------------------------------- #


class TaggerNet:
    """Por cada token: embedding de palabra + embedding de n-gramas de caracteres +
    embedding de 'forma'; se concatena una ventana de ±K tokens y se pasa por un MLP."""

    def __init__(self, n_words, n_chars, n_shapes, n_tags, dw=48, dc=32, ds=8, hidden=192, K=3, seed=0):
        rng = np.random.default_rng(seed)
        self.K = K
        self.D = dw + dc + ds
        self.cfg = dict(n_words=n_words, n_chars=n_chars, n_shapes=n_shapes, n_tags=n_tags,
                        dw=dw, dc=dc, ds=ds, hidden=hidden, K=K)
        W = 2 * K + 1
        self.p = {
            "Ew": (rng.standard_normal((n_words, dw)) * 0.1).astype(F),
            "Ec": (rng.standard_normal((n_chars, dc)) * 0.1).astype(F),
            "Es": (rng.standard_normal((n_shapes, ds)) * 0.1).astype(F),
            "W1": he(rng, W * self.D, hidden),
            "b1": np.zeros(hidden, F),
            "W2": he(rng, hidden, n_tags) * F(0.5),
            "b2": np.zeros(n_tags, F),
        }
        self.p["Ec"][0] = 0

    # -- construcción de lotes --------------------------------------------------
    def make_batch(self, sents):
        """sents: lista de (word_ids[L], char_ids[L,M], shape_ids[L]).  Devuelve arrays planos
        con K filas de relleno entre oraciones."""
        K = self.K
        M = sents[0][1].shape[1]
        w, c, s, real = [], [], [], []
        pad_w = np.zeros(K, np.int64)
        pad_c = np.zeros((K, M), np.int64)
        pad_s = np.zeros(K, np.int64)
        pos = 0
        w.append(pad_w); c.append(pad_c); s.append(pad_s); pos += K
        for wi, ci, si in sents:
            L = len(wi)
            w.append(wi); c.append(ci); s.append(si)
            real.extend(range(pos, pos + L))
            pos += L
            w.append(pad_w); c.append(pad_c); s.append(pad_s); pos += K
        real = np.array(real)
        win = real[:, None] + np.arange(-K, K + 1)[None, :]
        return np.concatenate(w), np.concatenate(c), np.concatenate(s), win

    def _embed(self, w, c, s):
        dw, dc = self.cfg["dw"], self.cfg["dc"]
        cm = (c > 0).astype(F)
        cc = np.maximum(cm.sum(1, keepdims=True), 1)
        ec = (self.p["Ec"][c] * cm[..., None]).sum(1) / cc
        R = np.concatenate([self.p["Ew"][w], ec, self.p["Es"][s]], 1)
        return R, (cm, cc)

    def forward(self, batch, train=False, rng=None, drop=0.2):
        w, c, s, win = batch
        R, aux = self._embed(w, c, s)
        N, Wn = win.shape
        X = R[win].reshape(N, Wn * self.D)
        z1 = X @ self.p["W1"] + self.p["b1"]
        h = np.maximum(z1, 0)
        dmask = None
        if train:
            dmask = (rng.random(h.shape) > drop).astype(F) / F(1 - drop)
            h = h * dmask
        logits = h @ self.p["W2"] + self.p["b2"]
        return logits, (batch, aux, X, z1, h, dmask)

    def predict_proba(self, batch):
        logits, _ = self.forward(batch, False)
        return softmax(logits)

    def loss_grads(self, batch, y, rng, class_w=None):
        logits, (batch, (cm, cc), X, z1, h, dmask) = self.forward(batch, True, rng)
        w, c, s, win = batch
        p = softmax(logits)
        N = len(y)
        wt = np.ones(N, F) if class_w is None else class_w[y]
        loss = float((-np.log(p[np.arange(N), y] + 1e-9) * wt).sum() / wt.sum())
        d = p.copy()
        d[np.arange(N), y] -= 1
        d *= (wt / wt.sum())[:, None]
        g = {"W2": h.T @ d, "b2": d.sum(0)}
        dh = (d @ self.p["W2"].T) * dmask * (z1 > 0)
        g["W1"] = X.T @ dh
        g["b1"] = dh.sum(0)
        dX = (dh @ self.p["W1"].T).reshape(N, win.shape[1], self.D)
        dR = np.zeros((len(w), self.D), F)
        np.add.at(dR, win, dX)
        dw, dc = self.cfg["dw"], self.cfg["dc"]
        gEw = np.zeros_like(self.p["Ew"]); np.add.at(gEw, w, dR[:, :dw])
        gEs = np.zeros_like(self.p["Es"]); np.add.at(gEs, s, dR[:, dw + dc:])
        gEc = np.zeros_like(self.p["Ec"])
        dec = (dR[:, dw:dw + dc] / cc)[:, None, :] * cm[..., None]
        np.add.at(gEc, c.reshape(-1), dec.reshape(-1, dc))
        gEc[0] = 0
        g.update(Ew=gEw, Ec=gEc, Es=gEs)
        return loss, g

    def state(self):
        return {**{f"p_{k}": v for k, v in self.p.items()}, "cfg": np.array(json.dumps(self.cfg))}

    @classmethod
    def from_state(cls, st):
        cfg = json.loads(str(st["cfg"]))
        m = cls(**cfg)
        for k in m.p:
            m.p[k] = st[f"p_{k}"].astype(F)
        return m
