"""sankey-explorer dashboard plugin — backend API routes (ADR-2 P0b).

Mounted at ``/api/plugins/sankey-explorer/`` by the dashboard plugin system
(see ``hermes_cli/web_server.py::_mount_plugin_api_routes``). The module is
exec'd standalone, so we add this directory to ``sys.path`` and import the
VENDORED engine (``engine/aggregate_source.py`` + ``engine/build_sankey.py``
+ ``engine/from_supabase.py`` + ``assets/template.html``) — the plugin is
self-contained and shares the exact same aggregate core as the gbautomation
``sankey3js`` skill and the Mini-side public-page build job.

Endpoints
---------
* ``GET /tables``            -> the PII-safe table catalog (no raw data).
* ``GET /chart?table=&dims=&weight=`` -> a single self-contained Sankey HTML
  (``text/html``) for embedding in an ``<iframe>`` from the React surface.

Security
--------
Like the kanban plugin, these routes sit behind the dashboard's session-token
auth middleware (``web_server.auth_middleware``): every ``/api/plugins/...``
request must present the session bearer token, or — for the ``<iframe>`` GET to
``/chart`` — the session cookie the dashboard sets when you load it. No new auth
surface is added.

PII-safety
----------
All output is AGGREGATE-ONLY by construction: the shared ``aggregate()`` core
pushes ``GROUP BY dims -> count(*) [, sum(weight)]`` into SQL (live) or
aggregates a committed fixture in-process (offline); raw rows never leave
Supabase and are never emitted. The table catalog excludes ``client-core`` and
any PII-bearing table/column.

Data source (host split)
------------------------
Supabase refs are DNS-blocked from the dev PC, so:

* ``SANKEY_EXPLORER_MODE=live``   -> always query live via ``gbauto-supabase``.
* ``SANKEY_EXPLORER_MODE=fixture``-> always read committed fixtures.
* unset (default) -> ``fixture`` when a committed fixture exists for the table
  (developable on the PC), otherwise ``live`` (the Mini path).

No secrets are ever printed; the ``gbauto-supabase`` CLI resolves its own
credentials. We never shell out with ``bash -x``.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Vendored engine import (self-contained plugin).
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent
_ENGINE = _HERE / "engine"
_ASSETS = _HERE / "assets"
_FIXTURES = _HERE / "fixtures"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

# aggregate_source pulls in from_supabase from the same dir.
import aggregate_source as agg  # noqa: E402
import build_sankey as bs  # noqa: E402

router = APIRouter()

# Point the vendored build_sankey at the plugin's own template/vendor dirs so
# the renderer is self-contained regardless of cwd.
bs.TEMPLATE = _ASSETS / "template.html"
bs.VENDOR = _ASSETS / "vendor"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _mode() -> str:
    m = (os.environ.get("SANKEY_EXPLORER_MODE") or "").strip().lower()
    return m if m in ("live", "fixture") else "auto"


def _fixture_for(table: str) -> Optional[Path]:
    """Return the committed fixture path for a table, if one exists."""
    fx = _FIXTURES / f"{table}.sample.json"
    return fx if fx.exists() else None


def _resolve_source(table: str) -> Optional[Path]:
    """Decide live (None) vs fixture (Path) per host-split policy."""
    mode = _mode()
    fx = _fixture_for(table)
    if mode == "live":
        return None
    if mode == "fixture":
        if fx is None:
            raise HTTPException(
                status_code=404,
                detail=f"no committed fixture for table '{table}' "
                       "(SANKEY_EXPLORER_MODE=fixture)",
            )
        return fx
    # auto: prefer fixture when present (PC-developable), else live (Mini).
    return fx


def _aggregate_safe(table: str, dims: list[str], weight: Optional[str],
                    fixture: Optional[Path]) -> dict:
    """Call the shared aggregate core, converting its hard-exits/errors into
    clean HTTP 400/500 so a bad request can never crash the dashboard worker."""
    try:
        return agg.aggregate(table, dims, weight=weight, fixture=fixture)
    except HTTPException:
        raise
    except SystemExit as exc:  # aggregate_source validates with sys.exit(msg)
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # CLI missing / DNS / parse — surface as 500
        log.warning("sankey-explorer aggregate failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"aggregate failed: {exc}")


def _render_html(result: dict, dims: list[str], weight: Optional[str]) -> str:
    """Render aggregate records into a self-contained Sankey HTML using the
    VENDORED build_sankey engine (template + importmap + placeholder fill).

    Records are already aggregate-only ({**dims, "n": count[, "<weight>": sum]}).
    Flows are sized by the requested weight column when given, else by ``n``
    (the group count) so the ribbons reflect row volume.
    """
    catalog = agg.table_catalog()
    meta = catalog.get(result["table"], {})
    unit = result.get("unit", "rows")
    title = f"{result.get('title', result['table'])} — Sankey"

    # Dim labels: Title-Case the key (matches build_sankey's default labelling).
    dim_specs = [{"key": k, "label": k.replace("_", " ").title()} for k in dims]

    # Size flows by the explicit weight sum if asked, else by the group count.
    if weight:
        weight_key = weight
        weight_label = weight.replace("_", " ").title()
    else:
        weight_key = "n"
        weight_label = unit.title()

    # Three columns for the Sankey; pad with repeats if fewer than 3 dims.
    default = list(dims[:3])
    while len(default) < 3:
        default.append(dims[len(default) % len(dims)])

    records = result["records"]
    payload = {"count": len(records), "records": records}

    html = bs.TEMPLATE.read_text(encoding="utf-8")
    import json as _json
    repl = {
        "__DATA__": _json.dumps(payload, separators=(",", ":")),
        "__DIMS__": _json.dumps(dim_specs),
        "__DEFAULTS__": _json.dumps(default[:3]),
        "__TITLE__": title,
        "__COUNT__": str(len(records)),
        "__UNIT__": unit,
        "__WEIGHT__": _json.dumps({"key": weight_key, "label": weight_label}),
        "__IMPORTMAP__": bs.build_importmap(False),
    }
    for token, val in repl.items():
        html = html.replace(token, val)
    leftover = [t for t in repl if t in html]
    if leftover:
        raise HTTPException(
            status_code=500,
            detail=f"chart render failed: unsubstituted placeholders {leftover}",
        )
    return html


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/tables")
async def tables():
    """Return the PII-safe table catalog: {table: {dims, weights, unit, title}}.

    This is metadata only — no row data, aggregate or otherwise. Each entry also
    advertises whether a committed offline fixture exists for it.
    """
    catalog = agg.table_catalog()
    out = {}
    for table, meta in catalog.items():
        out[table] = {
            "dims": meta.get("dims", []),
            "weights": meta.get("weights", []),
            "unit": meta.get("unit", "rows"),
            "title": meta.get("title", table),
            "has_fixture": _fixture_for(table) is not None,
        }
    return {
        "mode": _mode(),
        "tables": out,
        "excluded": agg.excluded_tables(),
    }


@router.get("/chart", response_class=HTMLResponse)
async def chart(
    table: str = Query(..., description="table from the PII-safe catalog"),
    dims: str = Query(..., description="comma-separated dims (2-3) to GROUP BY"),
    weight: Optional[str] = Query(None, description="optional numeric weight column to sum"),
):
    """Render a self-contained 3D Sankey HTML for (table, dims, weight).

    Returned as ``text/html`` for direct embedding in an ``<iframe>``.
    """
    dim_list = [d.strip() for d in dims.split(",") if d.strip()]
    if len(dim_list) < 2:
        raise HTTPException(status_code=400, detail="need at least 2 dims")
    if len(dim_list) > 3:
        raise HTTPException(status_code=400, detail="at most 3 dims (left/middle/right)")

    # Reject unknown / PII-excluded tables up front (independent of data mode)
    # so the error is "not in catalog" rather than "no fixture".
    if table not in agg.table_catalog():
        raise HTTPException(
            status_code=404,
            detail=f"table '{table}' is not in the PII-safe catalog",
        )

    fixture = _resolve_source(table)
    result = _aggregate_safe(table, dim_list, weight or None, fixture)
    html = _render_html(result, dim_list, weight or None)
    return HTMLResponse(content=html)
