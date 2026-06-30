/**
 * Sankey Explorer — Dashboard Plugin (ADR-2 P0b)
 *
 * Thin React surface: pick a PII-safe table, two/three dimensions, and an
 * optional weight, then render the chart in an <iframe> pointed at the
 * plugin's /chart endpoint. All heavy lifting (aggregate + 3D Sankey render)
 * happens server-side in plugin_api.py via the vendored sankey3js engine.
 *
 * Hand-written IIFE, no build step — mirrors plugins/kanban/dashboard/dist
 * format. Uses window.__HERMES_PLUGIN_SDK__ for React + primitives and
 * SDK.fetchJSON for authenticated calls; registers via window.__HERMES_PLUGINS__.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const { useState, useEffect, useMemo } = SDK.hooks;
  const C = SDK.components || {};
  const Button = C.Button || "button";
  const Label = C.Label || "label";

  const API = "/api/plugins/sankey-explorer";

  // A native <select> shim so we don't depend on the host exposing a styled
  // Select (the kanban Select API varies across host versions).
  function NativeSelect(props) {
    const { value, onChange, options, placeholder, disabled } = props;
    return h(
      "select",
      {
        className: "sankey-select",
        value: value || "",
        disabled: disabled,
        onChange: function (e) { onChange(e.target.value); },
      },
      placeholder ? h("option", { value: "" }, placeholder) : null,
      (options || []).map(function (o) {
        const val = typeof o === "string" ? o : o.value;
        const lbl = typeof o === "string" ? o : o.label;
        return h("option", { key: val, value: val }, lbl);
      })
    );
  }

  function SankeyExplorerPage() {
    const [catalog, setCatalog] = useState(null);
    const [mode, setMode] = useState("");
    const [err, setErr] = useState(null);
    const [table, setTable] = useState("");
    const [dimA, setDimA] = useState("");
    const [dimB, setDimB] = useState("");
    const [dimC, setDimC] = useState("");
    const [weight, setWeight] = useState("");
    const [src, setSrc] = useState("");

    // Load the PII-safe catalog once.
    useEffect(function () {
      SDK.fetchJSON(`${API}/tables`)
        .then(function (data) {
          setCatalog(data.tables || {});
          setMode(data.mode || "");
        })
        .catch(function (e) { setErr(String((e && e.message) || e)); });
    }, []);

    const tableMeta = useMemo(
      function () { return (catalog && table && catalog[table]) || null; },
      [catalog, table]
    );

    // When the table changes, seed sensible default dims/weight.
    useEffect(function () {
      if (!tableMeta) return;
      const dims = tableMeta.dims || [];
      setDimA(dims[0] || "");
      setDimB(dims[1] || "");
      setDimC(dims[2] || "");
      setWeight("");
      setSrc("");
    }, [table]);

    const tableOptions = useMemo(function () {
      if (!catalog) return [];
      return Object.keys(catalog).sort().map(function (t) {
        const m = catalog[t];
        return {
          value: t,
          label: (m.title || t) + (m.has_fixture ? "" : "  (live-only)"),
        };
      });
    }, [catalog]);

    const dimOptions = (tableMeta && tableMeta.dims) || [];
    const weightOptions = (tableMeta && tableMeta.weights) || [];

    function render() {
      const dims = [dimA, dimB, dimC].filter(Boolean);
      if (!table || dims.length < 2) {
        setErr("Pick a table and at least two dimensions.");
        return;
      }
      setErr(null);
      const params = new URLSearchParams();
      params.set("table", table);
      params.set("dims", dims.join(","));
      if (weight) params.set("weight", weight);
      // Same-origin GET; the dashboard session cookie authenticates the iframe.
      setSrc(`${API}/chart?${params.toString()}`);
    }

    if (err && !catalog) {
      return h("div", { className: "sankey-wrap" },
        h("div", { className: "sankey-error" }, "Failed to load catalog: " + err));
    }
    if (!catalog) {
      return h("div", { className: "sankey-wrap" },
        h("div", { className: "sankey-muted" }, "Loading table catalog…"));
    }

    return h("div", { className: "sankey-wrap" },
      h("div", { className: "sankey-header" },
        h("h1", { className: "sankey-title" }, "Sankey Explorer"),
        h("p", { className: "sankey-sub" },
          "Every PII-safe table as an interactive 3D Sankey. Aggregate-only " +
          "(GROUP BY -> count + sum); raw rows never leave Supabase." +
          (mode ? "  Mode: " + mode + "." : ""))
      ),
      h("div", { className: "sankey-controls" },
        h("div", { className: "sankey-field" },
          h(Label, { className: "sankey-label" }, "Table"),
          h(NativeSelect, {
            value: table, onChange: setTable,
            placeholder: "Choose a table…", options: tableOptions,
          })
        ),
        h("div", { className: "sankey-field" },
          h(Label, { className: "sankey-label" }, "Left"),
          h(NativeSelect, {
            value: dimA, onChange: setDimA, disabled: !tableMeta,
            placeholder: "dim…", options: dimOptions,
          })
        ),
        h("div", { className: "sankey-field" },
          h(Label, { className: "sankey-label" }, "Middle"),
          h(NativeSelect, {
            value: dimB, onChange: setDimB, disabled: !tableMeta,
            placeholder: "dim…", options: dimOptions,
          })
        ),
        h("div", { className: "sankey-field" },
          h(Label, { className: "sankey-label" }, "Right"),
          h(NativeSelect, {
            value: dimC, onChange: setDimC, disabled: !tableMeta,
            placeholder: "(optional)", options: dimOptions,
          })
        ),
        h("div", { className: "sankey-field" },
          h(Label, { className: "sankey-label" }, "Weight"),
          h(NativeSelect, {
            value: weight, onChange: setWeight,
            disabled: !tableMeta || weightOptions.length === 0,
            placeholder: weightOptions.length ? "count (default)" : "count only",
            options: weightOptions,
          })
        ),
        h("div", { className: "sankey-field sankey-field--btn" },
          h(Button, { onClick: render, disabled: !tableMeta }, "Render")
        )
      ),
      err ? h("div", { className: "sankey-error" }, err) : null,
      h("div", { className: "sankey-frame-wrap" },
        src
          ? h("iframe", {
              className: "sankey-frame",
              title: "Sankey chart",
              src: src,
            })
          : h("div", { className: "sankey-placeholder" },
              "Pick a table and dimensions, then press Render.")
      )
    );
  }

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("sankey-explorer", SankeyExplorerPage);
  }
})();
