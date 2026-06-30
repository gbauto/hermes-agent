#!/usr/bin/env python3
"""build_sankey.py — render an interactive 3D Sankey (three.js) from any records JSON.

The chart is a single self-contained HTML file: three columns of bars, each
column a button-selectable categorical dimension, ribbons sized by the count of
records sharing each pair of attributes. Proportional/Equalized size toggle.

Usage:
  python build_sankey.py --records data.json --out chart.html \
      --dims client:Client,status:Status,kind:Kind,prd_type:"PRD Type" \
      --default client,kind,priority --title "PRD Flow" --unit PRDs

--records accepts either a bare JSON array of flat objects, or an object with a
top-level "records" array (e.g. artifacts/prd-sankey/prd-sankey-data.json).

If --dims is omitted, low-cardinality string fields are auto-detected. If
--default is omitted, the three highest-variation dims are chosen.

Reuses the threejs-animation skill's clock/lerp/crossfade patterns. See SKILL.md.
"""
from __future__ import annotations
import argparse, base64, json, sys
from collections import Counter
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "template.html"
VENDOR = Path(__file__).resolve().parent.parent / "assets" / "vendor"

THREE_VERSION = "0.160.0"
THREE_CDN = f"https://unpkg.com/three@{THREE_VERSION}/build/three.module.js"
ORBIT_CDN = f"https://unpkg.com/three@{THREE_VERSION}/examples/jsm/controls/OrbitControls.js"

# Default (CDN) importmap — kept byte-identical to the historic template block so
# the default build output is unchanged.
CDN_IMPORTMAP = (
    '<script type="importmap">\n'
    '{ "imports": {\n'
    f'  "three": "{THREE_CDN}",\n'
    f'  "three/addons/": "https://unpkg.com/three@{THREE_VERSION}/examples/jsm/"\n'
    '}}\n'
    '</script>'
)


def _fetch_lib(url: str, cache_name: str) -> str:
    """Return library source, preferring a local vendor cache, else fetching once."""
    cache = VENDOR / cache_name
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    import urllib.request
    with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 (trusted CDN)
        src = resp.read().decode("utf-8")
    VENDOR.mkdir(parents=True, exist_ok=True)
    cache.write_text(src, encoding="utf-8")
    return src


def build_importmap(inline: bool) -> str:
    """CDN importmap by default; inlined data-URI modules when --inline-three.

    Degrades gracefully: if the libs cannot be fetched (offline at build time)
    we warn and fall back to the CDN importmap so the default path never breaks.
    """
    if not inline:
        return CDN_IMPORTMAP
    try:
        three_src = _fetch_lib(THREE_CDN, f"three-{THREE_VERSION}.module.js")
        orbit_src = _fetch_lib(ORBIT_CDN, f"OrbitControls-{THREE_VERSION}.js")
    except Exception as e:  # network/SSL/timeout — keep the build working
        print(
            f"warning: --inline-three could not fetch three.js ({e}); "
            "falling back to CDN importmap (page will need network when viewed). "
            f"To inline offline, pre-place the libs in {VENDOR}/",
            file=sys.stderr,
        )
        return CDN_IMPORTMAP

    def uri(s: str) -> str:  # base64 data URI (alphabet has no quotes/underscores -> JSON-safe)
        return "data:text/javascript;base64," + base64.b64encode(s.encode("utf-8")).decode("ascii")

    # The main module imports the exact addon path, so map that key precisely;
    # OrbitControls' own bare `from "three"` resolves via the "three" entry.
    return (
        '<script type="importmap">\n'
        '{ "imports": {\n'
        f'  "three": "{uri(three_src)}",\n'
        f'  "three/addons/controls/OrbitControls.js": "{uri(orbit_src)}"\n'
        '}}\n'
        '</script>'
    )


def load_records(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    recs = raw["records"] if isinstance(raw, dict) and "records" in raw else raw
    if not isinstance(recs, list) or not recs:
        sys.exit("error: --records must be a non-empty array (or {records:[...]})")
    return recs


def norm(v):
    return "(none)" if v is None or v == "" else str(v)


def to_num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def normalize_records(recs: list[dict], keys: list[str], weight_key: str | None) -> list[dict]:
    out = []
    for r in recs:
        d = {k: norm(r.get(k)) for k in keys}
        if weight_key:
            d[weight_key] = to_num(r.get(weight_key))
        out.append(d)
    return out


def auto_dims(recs: list[dict], max_card: int = 40) -> list[tuple[str, str]]:
    """Pick string-ish fields with 2..max_card distinct values, by variation."""
    fields = {}
    for r in recs:
        for k, v in r.items():
            if isinstance(v, (dict, list)):
                continue
            fields.setdefault(k, Counter())[norm(v)] += 1
    scored = []
    for k, c in fields.items():
        card = len(c)
        if 2 <= card <= max_card:
            # variation score: prefer dims that actually split the data
            top = c.most_common(1)[0][1]
            scored.append((card * (1 - top / sum(c.values())), k))
    scored.sort(reverse=True)
    return [(k, k.replace("_", " ").title()) for _, k in scored]


def parse_dims(spec: str) -> list[tuple[str, str]]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            key, label = part.split(":", 1)
            out.append((key.strip(), label.strip().strip('"')))
        else:
            out.append((part, part.replace("_", " ").title()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--dims", help="key:Label,key:Label,... (default: auto-detect)")
    ap.add_argument("--default", help="three keys for left,middle,right (default: top 3)")
    ap.add_argument("--title", default="3D Sankey")
    ap.add_argument("--unit", default="records")
    ap.add_argument("--weight", help="numeric field to size flows by (default: count of records)")
    ap.add_argument("--weight-label", help="display label for the weight measure (default: titled field name)")
    ap.add_argument("--inline-three", action="store_true",
                    help="inline three.js + OrbitControls as data-URI modules for offline use "
                         "(default: CDN importmap). Degrades to CDN if the libs can't be fetched.")
    args = ap.parse_args()

    recs = load_records(args.records)
    dims = parse_dims(args.dims) if args.dims else auto_dims(recs)
    if len(dims) < 2:
        sys.exit("error: need at least 2 usable dimensions (got %d); pass --dims" % len(dims))

    keys = [k for k, _ in dims]
    weight_key = args.weight
    if weight_key and weight_key in keys:
        sys.exit(f"error: --weight '{weight_key}' is also a dimension; pick a numeric field not in --dims")
    weight_label = args.weight_label or (weight_key.replace("_", " ").title() if weight_key else None)
    records = normalize_records(recs, keys, weight_key)

    if args.default:
        default = [d.strip() for d in args.default.split(",")][:3]
        for d in default:
            if d not in keys:
                sys.exit(f"error: --default '{d}' not in dims {keys}")
    else:
        default = keys[:3]
    while len(default) < 3:                      # pad if <3 dims supplied
        default.append(keys[len(default) % len(keys)])

    payload = {"count": len(records), "records": records}
    html = TEMPLATE.read_text(encoding="utf-8")
    repl = {
        "__DATA__": json.dumps(payload, separators=(",", ":")),
        "__DIMS__": json.dumps([{"key": k, "label": lbl} for k, lbl in dims]),
        "__DEFAULTS__": json.dumps(default[:3]),
        "__TITLE__": args.title,
        "__COUNT__": str(len(records)),
        "__UNIT__": args.unit,
        "__WEIGHT__": json.dumps({"key": weight_key, "label": weight_label} if weight_key else None),
        "__IMPORTMAP__": build_importmap(args.inline_three),
    }
    for token, val in repl.items():
        html = html.replace(token, val)
    leftover = [t for t in repl if t in html]
    if leftover:
        sys.exit(f"error: unsubstituted placeholders remain: {leftover}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    wnote = f" · weight={weight_key}" if weight_key else ""
    print(f"wrote {args.out} ({len(html):,} bytes) · {len(records)} {args.unit} · "
          f"dims={keys} · default={default[:3]}{wnote}")


if __name__ == "__main__":
    main()
