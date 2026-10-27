"""Modelo de instrucciones: clasificador de intención + etiquetador de entidades.

Ambas redes son propias (ver nn.py) y se entrenan con datos de datagen.py.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from .datagen import INTENTS, TAGS, TAG2ID
from .nn import BagMLP, TaggerNet
from .text import N_SHAPES, Token, char_ngrams, norm, shape_id, stable_hash, tokenize

M_CHARS = 24
N_CHAR_BUCKETS = 8192
N_BIGRAM_BUCKETS = 4096
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*$")


def word_key(surface: str) -> str:
    if NUM_RE.match(surface):
        return "<num>"
    if "@" in surface:
        return "<email>"
    return norm(surface)


def char_ids_for(word: str) -> np.ndarray:
    ids = np.zeros(M_CHARS, np.int64)
    if word in ("<num>", "<email>", "%"):
        return ids
    for i, g in enumerate(char_ngrams(word, cap=M_CHARS)):
        ids[i] = 1 + stable_hash(g, N_CHAR_BUCKETS - 1)
    return ids


def featurize_tagger(surfaces: List[str], vocab: Dict[str, int]):
    keys = [word_key(t) for t in surfaces]
    w = np.array([vocab.get(k, 1) for k in keys], np.int64)
    c = np.stack([char_ids_for(k) for k in keys]) if keys else np.zeros((0, M_CHARS), np.int64)
    s = np.array([shape_id(t) for t in surfaces], np.int64)
    return w, c, s


def intent_ids(surfaces: List[str], vocab: Dict[str, int]) -> List[int]:
    keys = [word_key(t) for t in surfaces]
    nv = len(vocab) + 1
    ids = [vocab.get(k, 1) + 0 for k in keys]
    for a, b in zip(keys, keys[1:]):
        ids.append(nv + 1 + stable_hash(a + "_" + b, N_BIGRAM_BUCKETS))
    base = nv + 1 + N_BIGRAM_BUCKETS
    for k in keys:
        if k in ("<num>", "<email>", "%"):
            continue
        for g in char_ngrams(k, ns=(4,), cap=12):
            ids.append(base + stable_hash(g, N_CHAR_BUCKETS))
    return ids or [1]


def intent_vocab_size(vocab):
    return len(vocab) + 1 + N_BIGRAM_BUCKETS + N_CHAR_BUCKETS + 1


def pad_ids(rows: List[List[int]]) -> np.ndarray:
    L = max(len(r) for r in rows)
    out = np.zeros((len(rows), L), np.int64)
    for i, r in enumerate(rows):
        out[i, : len(r)] = r
    return out


@dataclass
class Span:
    tag: str
    start: int
    end: int           # exclusivo
    text: str


@dataclass
class Parsed:
    text: str
    tokens: List[Token]
    intent: str
    intent_conf: float
    intent_probs: Dict[str, float]
    tags: List[str]
    tag_conf: List[float]
    spans: List[Span]


def decode_spans(tokens: List[Token], tags: List[str]) -> List[Span]:
    spans, cur = [], None
    for i, tg in enumerate(tags):
        if tg == "O":
            cur = None
            continue
        pre, ent = tg.split("-", 1)
        if pre == "B" or cur is None or cur.tag != ent:
            cur = Span(ent, i, i + 1, tokens[i].surface)
            spans.append(cur)
        else:
            cur.end = i + 1
            cur.text += " " + tokens[i].surface
    return spans


class InstructionModel:
    def __init__(self, vocab: Dict[str, int], intent_net: BagMLP, tagger: TaggerNet):
        self.vocab, self.intent_net, self.tagger = vocab, intent_net, tagger

    # -- inferencia -----------------------------------------------------------------
    def parse(self, text: str) -> Parsed:
        toks = tokenize(text)
        surf = [t.surface for t in toks]
        if not toks:
            return Parsed(text, [], "unknown", 1.0, {}, [], [], [])
        ids = pad_ids([intent_ids(surf, self.vocab)])
        probs = self.intent_net.predict_proba(ids)[0]
        k = int(probs.argmax())
        w, c, s = featurize_tagger(surf, self.vocab)
        batch = self.tagger.make_batch([(w, c, s)])
        tp = self.tagger.predict_proba(batch)
        tag_ids = tp.argmax(1)
        tags = [TAGS[i] for i in tag_ids]
        tags = _repair_bio(tags)
        return Parsed(
            text=text, tokens=toks, intent=INTENTS[k], intent_conf=float(probs[k]),
            intent_probs={INTENTS[i]: float(p) for i, p in enumerate(probs)},
            tags=tags, tag_conf=[float(tp[i, t]) for i, t in enumerate(tag_ids)],
            spans=decode_spans(toks, tags),
        )

    # -- persistencia ---------------------------------------------------------------
    def save(self, directory: str):
        os.makedirs(directory, exist_ok=True)
        np.savez_compressed(os.path.join(directory, "intent.npz"), **self.intent_net.state())
        np.savez_compressed(os.path.join(directory, "tagger.npz"), **self.tagger.state())
        with open(os.path.join(directory, "vocab.json"), "w", encoding="utf-8") as fh:
            json.dump(self.vocab, fh, ensure_ascii=False)

    @classmethod
    def load(cls, directory: str):
        with open(os.path.join(directory, "vocab.json"), encoding="utf-8") as fh:
            vocab = json.load(fh)
        i = np.load(os.path.join(directory, "intent.npz"))
        t = np.load(os.path.join(directory, "tagger.npz"))
        return cls(vocab, BagMLP.from_state(i), TaggerNet.from_state(t))


def _repair_bio(tags: List[str]) -> List[str]:
    """Una 'I-X' sin 'B-X' previo se convierte en 'B-X'."""
    out, prev = [], "O"
    for t in tags:
        if t.startswith("I-") and (prev == "O" or prev[2:] != t[2:]):
            t = "B-" + t[2:]
        out.append(t)
        prev = t
    return out
