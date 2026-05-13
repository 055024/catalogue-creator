"""
Dealer-price matcher: parses an Excel price list and fuzzy-matches its model
names against product names extracted from a catalogue PDF.

Normalization:
- lowercase, drop non-alphanumeric
- remove single-word brand prefixes (colorfit, noisefit, noise)
- treat "|" as a separator (it's a line-break artifact in the PDF)

Matching: rapidfuzz.token_set_ratio (handles word reorder + partial overlap).
"""

import re
from io import BytesIO

import openpyxl
from rapidfuzz import process, fuzz


BRAND_PREFIXES = {"colorfit", "noisefit", "noise"}
GENERIC_TOKENS = {"smartwatch", "smart", "watch", "tws", "earbuds",
                  "earphones", "headphones", "neckband"}


def normalize(s: str) -> str:
    if not s:
        return ""
    s = s.lower().replace("|", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    parts = s.split()
    while parts and parts[0] in BRAND_PREFIXES:
        parts.pop(0)
    parts = [t for t in parts if t not in GENERIC_TOKENS]
    return " ".join(parts)


def _expand_variants(name: str):
    """A row like 'Diva (Pearl white, Rose pink)' becomes two entries."""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*(.*)$", name)
    if not m:
        return [name]
    prefix, inside, suffix = m.groups()
    parts = [p.strip() for p in inside.split(",") if p.strip()]
    if not parts:
        return [name]
    return [f"{prefix} {p} {suffix}".strip() for p in parts]


def parse_price_list(xlsx_bytes: bytes):
    """
    Returns list of {category, model, dealer_price, normalized}.
    Auto-detects header row (containing 'MODEL'), category and price columns.
    """
    wb = openpyxl.load_workbook(BytesIO(xlsx_bytes), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    header_idx = None
    for i, row in enumerate(rows):
        if row and any(c and "MODEL" in str(c).upper() for c in row):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Price list missing 'MODELS' header row.")

    header = [str(c).strip().lower() if c else "" for c in rows[header_idx]]
    cat_i = next((i for i, h in enumerate(header) if "categ" in h), None)
    mod_i = next((i for i, h in enumerate(header) if "model" in h), None)
    dp_i = next((i for i, h in enumerate(header)
                 if "dealer" in h or ("price" in h and "mrp" not in h)), None)
    if mod_i is None or dp_i is None:
        raise ValueError("Price list needs MODELS and Dealer Price columns.")

    items = []
    for row in rows[header_idx + 1:]:
        if not row or len(row) <= max(mod_i, dp_i):
            continue
        if not row[mod_i] or row[dp_i] is None:
            continue
        category = ""
        if cat_i is not None and cat_i < len(row) and row[cat_i]:
            category = str(row[cat_i]).strip()
        model_raw = str(row[mod_i]).strip()
        try:
            dp = float(row[dp_i])
        except (TypeError, ValueError):
            continue
        for variant in _expand_variants(model_raw):
            items.append({
                "category": category,
                "model": variant,
                "dealer_price": dp,
                "normalized": normalize(variant),
            })
    return items


def build_lookup(items):
    """Returns (keys_list, key_to_first_item_index)."""
    keys = []
    key_to_idx = {}
    for i, it in enumerate(items):
        k = it["normalized"]
        if not k or k in key_to_idx:
            continue
        keys.append(k)
        key_to_idx[k] = i
    return keys, key_to_idx


def _number_set(s: str) -> set:
    return set(re.findall(r"\d+", s))


def _alpha_token_set(s: str) -> set:
    return {t for t in s.split() if t and not t.isdigit() and len(t) > 1}


def _composite_score(a: str, b: str) -> float:
    """
    Length-aware score with model-number sanity check.

    - min(token_sort_ratio, WRatio) avoids token_set_ratio's subset
      loophole where 'pulse 2 pro elite' 100-matches 'pulse 2 pro'.
    - Penalty when numeric tokens differ (model 3 vs model 4 are
      different SKUs even though strings are 90%+ similar).
    - Smaller penalty when single-letter codes diverge (Buds R1 vs Buds E1).
    """
    base = min(fuzz.token_sort_ratio(a, b), fuzz.WRatio(a, b))
    na, nb = _number_set(a), _number_set(b)
    if (na or nb) and na != nb:
        base -= 20 if (na and nb) else 6
    # Single-character tokens like 'r1' vs 'e1' — fuzz scores them similarly.
    # If short alpha-numeric codes differ, penalize.
    short_a = {t for t in a.split() if 2 <= len(t) <= 3 and any(c.isdigit() for c in t)}
    short_b = {t for t in b.split() if 2 <= len(t) <= 3 and any(c.isdigit() for c in t)}
    if (short_a or short_b) and short_a != short_b:
        base -= 12
    return max(0.0, base)


def match_one(catalog_name, keys, key_to_idx, items, threshold=100):
    """Return (matched_item, score) or (None, score)."""
    norm = normalize(catalog_name)
    if not norm or not keys:
        return None, 0
    candidates = process.extract(norm, keys, scorer=fuzz.token_set_ratio, limit=5)
    if not candidates:
        return None, 0
    rescored = [(k, _composite_score(norm, k)) for k, _, _ in candidates]
    rescored.sort(key=lambda x: x[1], reverse=True)
    matched_key, score = rescored[0]
    if score < threshold:
        return None, score
    return items[key_to_idx[matched_key]], score


def map_catalog(catalog_names, items, threshold=100):
    """
    For each catalog name, return (item_or_none, score, matched_normalized_key).
    Also returns the set of price-list normalized-keys that got matched.
    """
    keys, key_to_idx = build_lookup(items)
    matched_keys = set()
    results = []
    for name in catalog_names:
        item, score = match_one(name, keys, key_to_idx, items, threshold)
        results.append((item, score))
        if item is not None:
            matched_keys.add(item["normalized"])
    return results, matched_keys


def missing_items(items, matched_keys):
    """Items in the price list whose normalized key was never matched."""
    seen = set()
    out = []
    for it in items:
        k = it["normalized"]
        if not k or k in matched_keys or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out
