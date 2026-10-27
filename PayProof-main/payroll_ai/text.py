"""Preprocesamiento de texto: normalización, tokenización y números en español.

Esto es código determinista (reglas), NO es parte del modelo entrenado. Convierte
"diez por ciento" -> "10 %", "tres millones" -> "3000000", etc., para que el modelo
solo vea números ya escritos con dígitos.
"""
from __future__ import annotations

import re
import unicodedata
import zlib
from dataclasses import dataclass
from typing import List, Optional

# --------------------------------------------------------------------------- #
# Utilidades básicas
# --------------------------------------------------------------------------- #


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s: str) -> str:
    """minúsculas + sin acentos + espacios colapsados."""
    return re.sub(r"\s+", " ", strip_accents(s).lower()).strip()


def stable_hash(s: str, mod: int) -> int:
    """Hash determinista entre procesos (hash() de Python cambia en cada ejecución)."""
    return zlib.crc32(s.encode("utf-8")) % mod


# --------------------------------------------------------------------------- #
# Números
# --------------------------------------------------------------------------- #

_UNITS = {
    "cero": 0, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29,
}
_TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
         "ochenta": 80, "noventa": 90}
_HUNDREDS = {"cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300,
             "cuatrocientos": 400, "quinientos": 500, "seiscientos": 600,
             "setecientos": 700, "ochocientos": 800, "novecientos": 900}
_ONES_AMBIG = {"un": 1, "uno": 1, "una": 1}  # solo cuentan si van seguidos de mil/millón
_MULT = {"mil": 1000, "millon": 1_000_000, "millones": 1_000_000}

_NUMWORDS = set(_UNITS) | set(_TENS) | set(_HUNDREDS) | set(_MULT)

_NUM_CORE = r"\d+(?:[.,]\d+)*"
TOKEN_RE = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"          # emails
    rf"|{_NUM_CORE}"                           # números con separadores
    r"|[^\W\d_]+(?:['’-][^\W\d_]+)*"          # palabras
    r"|%|\S",
    re.U,
)


@dataclass
class Token:
    surface: str   # texto tal como se mostrará / usará como valor
    norm: str      # minúsculas sin acentos


def parse_number(s: str) -> Optional[float]:
    """Interpreta '3.200.000', '3,200,000', '3200000.5', '$ 3.200.000,50', '1 200 000'.

    Regla para un único separador: si aparece más de una vez, o si lo siguen exactamente
    3 dígitos, es de miles; en otro caso es decimal. ('1.500' -> 1500, '2.5' -> 2.5)
    """
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    neg = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = re.sub(r"[^\d,.\s]", "", t).replace(" ", "")
    if not t or not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        thou = "." if dec == "," else ","
        t = t.replace(thou, "").replace(dec, ".")
    elif "," in t or "." in t:
        sep = "," if "," in t else "."
        parts = t.split(sep)
        if len(parts) > 2 or len(parts[-1]) == 3:
            t = "".join(parts)
        else:
            t = ".".join(parts)
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _words_value(words: List[str]) -> Optional[int]:
    """Valor de una secuencia de palabras numéricas (ya sin acentos)."""
    total, current = 0, 0
    seen = False
    for w in words:
        if w == "y":
            continue
        if w in _UNITS:
            current += _UNITS[w]
        elif w in _ONES_AMBIG:
            current += 1
        elif w in _TENS:
            current += _TENS[w]
        elif w in _HUNDREDS:
            current += _HUNDREDS[w]
        elif w == "mil":
            total += (current or 1) * 1000
            current = 0
        elif w in ("millon", "millones"):
            total += (current or 1) * 1_000_000
            current = 0
        else:
            return None
        seen = True
    return total + current if seen else None


def tokenize(text: str) -> List[Token]:
    """Tokeniza y normaliza números (palabras -> dígitos, 'por ciento' -> '%')."""
    raw = TOKEN_RE.findall(text)
    toks = [Token(t, norm(t)) for t in raw]

    out: List[Token] = []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        # "por ciento" / "porciento" -> %
        if t.norm == "por" and i + 1 < n and toks[i + 1].norm == "ciento":
            out.append(Token("%", "%"))
            i += 2
            continue
        if t.norm == "porciento":
            out.append(Token("%", "%"))
            i += 1
            continue
        # "medio millón"
        if t.norm == "medio" and i + 1 < n and toks[i + 1].norm in ("millon", "millones"):
            out.append(Token("500000", "500000"))
            i += 2
            continue
        # dígitos seguidos de mil / millones:  "3 millones", "200 mil", "2,5 millones"
        if re.fullmatch(_NUM_CORE, t.surface) and i + 1 < n and toks[i + 1].norm in _MULT:
            base = parse_number(t.surface)
            if base is not None:
                v = base * _MULT[toks[i + 1].norm]
                s = str(int(v)) if float(v).is_integer() else str(v)
                out.append(Token(s, s))
                i += 2
                continue
        # secuencia de palabras numéricas
        starts_num = t.norm in _NUMWORDS or (
            t.norm in _ONES_AMBIG and i + 1 < n and toks[i + 1].norm in _MULT
        )
        if starts_num:
            j = i
            words = []
            while j < n:
                w = toks[j].norm
                is_num = w in _NUMWORDS or w in _ONES_AMBIG or (
                    w == "y" and words and j + 1 < n
                    and (toks[j + 1].norm in _UNITS or toks[j + 1].norm in _ONES_AMBIG)
                )
                if not is_num:
                    break
                words.append(w)
                j += 1
            val = _words_value(words)
            if val is not None and not (len(words) == 1 and words[0] in ("y",)):
                out.append(Token(str(val), str(val)))
                i = j
                continue
        out.append(t)
        i += 1
    return out


def shape_id(tok: str) -> int:
    """Rasgo de forma del token (se usa como característica del modelo)."""
    if tok == "%":
        return 1
    if "@" in tok:
        return 2
    if re.fullmatch(_NUM_CORE, tok):
        digits = len(re.sub(r"\D", "", tok))
        if digits <= 2:
            return 3
        if digits <= 4:
            return 4
        return 5 if digits <= 7 else 6
    if tok.isalpha():
        if tok.isupper() and len(tok) > 1:
            return 7
        if tok[0].isupper():
            return 8
        return 9
    if any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok):
        return 10
    if re.fullmatch(r"\W", tok):
        return 11
    return 12


N_SHAPES = 13


def char_ngrams(word: str, ns=(3, 4), cap: int = 24) -> List[str]:
    w = f"<{word}>"
    grams = []
    for n in ns:
        for i in range(max(0, len(w) - n + 1)):
            grams.append(w[i:i + n])
    return grams[:cap]
