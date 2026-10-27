"""Lectura/escritura de CSV sin estructura definida.

Detecta delimitador, filas de título previas, fila de encabezado (o su ausencia),
filas de totales al final, filas desiguales y el formato numérico de cada columna
(para escribir los valores nuevos con el mismo estilo: "3.200.000", "$ 3,200,000"…).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .text import parse_number

TOTAL_RE = re.compile(r"^\s*(gran\s+)?(total|subtotal|suma|totales)\b", re.I)


@dataclass
class NumFormat:
    prefix: str = ""
    suffix: str = ""
    thousands: str = ""
    decimal: str = ""
    decimals: int = 0

    def render(self, v: float) -> str:
        if self.decimals == 0:
            v = round(v)
        else:
            v = round(v, self.decimals)
        neg = v < 0
        v = abs(v)
        ip = int(v)
        s = f"{ip:d}"
        if self.thousands:
            parts = []
            while s:
                parts.append(s[-3:]); s = s[:-3]
            s = self.thousands.join(reversed(parts))
        if self.decimals:
            frac = f"{v - ip:.{self.decimals}f}"[2:]
            s += (self.decimal or ".") + frac
        return ("-" if neg else "") + self.prefix + s + self.suffix


def detect_format(cells: List[str]) -> NumFormat:
    pre, suf, th, dec, nd = {}, {}, {}, {}, {}
    for c in cells:
        c = c.strip()
        m = re.fullmatch(r"([^\d\-]*?)\s*(-?[\d.,\s]*\d)\s*(\D*)", c)
        if not m:
            continue
        p, body, sf = m.group(1), m.group(2), m.group(3)
        # conserva el espacio original entre prefijo y número
        if p and c[len(p):len(p) + 1] == " ":
            p += " "
        pre[p] = pre.get(p, 0) + 1
        suf[sf] = suf.get(sf, 0) + 1
        body = body.replace(" ", "")
        t = d = ""
        n = 0
        if "," in body and "." in body:
            d = "," if body.rfind(",") > body.rfind(".") else "."
            t = "." if d == "," else ","
            n = len(body.split(d)[-1])
        elif "," in body or "." in body:
            sep = "," if "," in body else "."
            parts = body.split(sep)
            if len(parts) > 2 or len(parts[-1]) == 3:
                t = sep
            else:
                d = sep
                n = len(parts[-1])
        th[t] = th.get(t, 0) + 1
        dec[d] = dec.get(d, 0) + 1
        nd[n] = nd.get(n, 0) + 1
    top = lambda dct, default="": max(dct, key=dct.get) if dct else default
    return NumFormat(top(pre), top(suf), top(th), top(dec), top(nd, 0))


@dataclass
class Table:
    delimiter: str = ","
    pre: List[List[str]] = field(default_factory=list)       # filas de título antes del encabezado
    header: List[str] = field(default_factory=list)
    has_header: bool = True
    rows: List[List[str]] = field(default_factory=list)      # filas de datos
    post: List[List[str]] = field(default_factory=list)      # totales/pie de tabla
    newline: str = "\n"

    @property
    def ncols(self) -> int:
        return len(self.header)

    def col(self, j: int) -> List[str]:
        return [r[j] for r in self.rows]

    def copy(self) -> "Table":
        return Table(self.delimiter, [r[:] for r in self.pre], self.header[:], self.has_header,
                     [r[:] for r in self.rows], [r[:] for r in self.post], self.newline)

    # ------------------------------------------------------------------ lectura
    @classmethod
    def from_text(cls, text: str) -> "Table":
        text = text.lstrip("﻿")
        newline = "\r\n" if "\r\n" in text else "\n"
        sample = "\n".join(text.splitlines()[:50])
        delim = sniff_delimiter(sample)
        rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
        if not rows:
            return cls(delim, newline=newline)
        modal = _modal_width(rows)
        # filas iniciales "de título": una sola celda con texto o con muy pocas columnas
        start = 0
        while start < len(rows) - 1 and (len([c for c in rows[start] if c.strip()]) < max(2, 0.5 * modal)):
            start += 1
        body = rows[start:]
        end = len(body)
        while end > 1 and (TOTAL_RE.match(next((c for c in body[end - 1] if c.strip()), "")) or
                           len([c for c in body[end - 1] if c.strip()]) < max(2, 0.4 * modal)):
            end -= 1
        post = body[end:]
        body = body[:end]
        width = max(len(r) for r in body)
        body = [r + [""] * (width - len(r)) for r in body]
        has_header = _looks_like_header(body)
        if has_header:
            header, data = body[0], body[1:]
        else:
            header, data = [f"col{i + 1}" for i in range(width)], body
        header = [h.strip() if h.strip() else f"col{i + 1}" for i, h in enumerate(header)]
        post = [r + [""] * (width - len(r)) if len(r) < width else r for r in post]
        return cls(delim, [r for r in rows[:start]], header, has_header, data, post, newline)

    # ------------------------------------------------------------------ escritura
    def to_text(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=self.delimiter, lineterminator=self.newline)
        for r in self.pre:
            w.writerow(r)
        if self.has_header:
            w.writerow(self.header)
        for r in self.rows:
            w.writerow(r)
        for r in self.post:
            w.writerow(r)
        return buf.getvalue()


def sniff_delimiter(sample: str) -> str:
    lines = [l for l in sample.splitlines() if l.strip()][:30]
    best, best_score = ",", -1.0
    for d in [",", ";", "\t", "|"]:
        counts = [len(next(csv.reader([l], delimiter=d))) for l in lines]
        if not counts or max(counts) < 2:
            continue
        mode = max(set(counts), key=counts.count)
        score = counts.count(mode) / len(counts) * min(mode, 6)
        if score > best_score:
            best, best_score = d, score
    return best


def _modal_width(rows) -> int:
    ws = [len(r) for r in rows]
    return max(set(ws), key=ws.count)


def _is_num(s: str) -> bool:
    s = s.strip()
    return bool(s) and parse_number(s) is not None and len(re.sub(r"[^A-Za-z]", "", s)) <= 3 and bool(re.search(r"\d", s))


def _looks_like_header(body) -> bool:
    if len(body) < 2:
        return True
    first, rest = body[0], body[1:11]
    width = len(first)
    num_cols = [j for j in range(width) if sum(_is_num(r[j]) for r in rest) >= 0.6 * len(rest)]
    if num_cols:
        # encabezado si alguna columna numérica tiene arriba una celda no numérica
        return any(first[j].strip() and not _is_num(first[j]) for j in num_cols)
    labelish = all((not c.strip()) or (len(c) <= 40 and not re.search(r"\d{3,}", c) and "@" not in c) for c in first)
    repeated = any(first == r for r in rest)
    return labelish and not repeated and len({c.strip().lower() for c in first}) == width
