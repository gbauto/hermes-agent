import type { DashboardTheme, ThemeTypography, ThemeLayout } from "./types";

/**
 * Built-in dashboard themes.
 *
 * Each theme defines its own palette, typography, and layout so switching
 * themes produces visible changes beyond just color — fonts, density, and
 * corner-radius all shift to match the theme's personality.
 *
 * Theme names must stay in sync with the backend's
 * `_BUILTIN_DASHBOARD_THEMES` list in `hermes_cli/web_server.py`.
 */

// ---------------------------------------------------------------------------
// Shared typography / layout presets
// ---------------------------------------------------------------------------

/** Default system stack — neutral, safe fallback for every platform. */
const SYSTEM_SANS =
  'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const SYSTEM_MONO =
  'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace';

const DEFAULT_TYPOGRAPHY: ThemeTypography = {
  fontSans: SYSTEM_SANS,
  fontMono: SYSTEM_MONO,
  baseSize: "15px",
  lineHeight: "1.55",
  letterSpacing: "0",
};

const DEFAULT_LAYOUT: ThemeLayout = {
  radius: "0.5rem",
  density: "comfortable",
};

// ---------------------------------------------------------------------------
// Themes
// ---------------------------------------------------------------------------

export const defaultTheme: DashboardTheme = {
  name: "default",
  label: "Hermes Teal",
  description: "Classic dark teal — the canonical Hermes look",
  palette: {
    background: { hex: "#041c1c", alpha: 1 },
    midground: { hex: "#ffe6cb", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(255, 189, 56, 0.35)",
    noiseOpacity: 1,
  },
  typography: DEFAULT_TYPOGRAPHY,
  layout: DEFAULT_LAYOUT,
};

export const midnightTheme: DashboardTheme = {
  name: "midnight",
  label: "Midnight",
  description: "Deep blue-violet with cool accents",
  palette: {
    background: { hex: "#0a0a1f", alpha: 1 },
    midground: { hex: "#d4c8ff", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(167, 139, 250, 0.32)",
    noiseOpacity: 0.8,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Inter", ${SYSTEM_SANS}`,
    fontMono: `"JetBrains Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap",
    letterSpacing: "-0.005em",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0.75rem",
  },
};

export const emberTheme: DashboardTheme = {
  name: "ember",
  label: "Ember",
  description: "Warm crimson and bronze — forge vibes",
  palette: {
    background: { hex: "#1a0a06", alpha: 1 },
    midground: { hex: "#ffd8b0", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(249, 115, 22, 0.38)",
    noiseOpacity: 1,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Spectral", Georgia, "Times New Roman", serif`,
    fontMono: `"IBM Plex Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Spectral:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;700&display=swap",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0.25rem",
  },
  colorOverrides: {
    destructive: "#c92d0f",
    warning: "#f97316",
  },
};

export const monoTheme: DashboardTheme = {
  name: "mono",
  label: "Mono",
  description: "Clean grayscale — minimal and focused",
  palette: {
    background: { hex: "#0e0e0e", alpha: 1 },
    midground: { hex: "#eaeaea", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(255, 255, 255, 0.1)",
    noiseOpacity: 0.6,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"IBM Plex Sans", ${SYSTEM_SANS}`,
    fontMono: `"IBM Plex Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0",
  },
};

export const cyberpunkTheme: DashboardTheme = {
  name: "cyberpunk",
  label: "Cyberpunk",
  description: "Neon green on black — matrix terminal",
  palette: {
    background: { hex: "#040608", alpha: 1 },
    midground: { hex: "#9bffcf", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(0, 255, 136, 0.22)",
    noiseOpacity: 1.2,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Share Tech Mono", "JetBrains Mono", ${SYSTEM_MONO}`,
    fontMono: `"Share Tech Mono", "JetBrains Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=JetBrains+Mono:wght@400;700&display=swap",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0",
  },
  colorOverrides: {
    success: "#00ff88",
    warning: "#ffd700",
    destructive: "#ff0055",
  },
};

export const roseTheme: DashboardTheme = {
  name: "rose",
  label: "Rosé",
  description: "Soft pink and warm ivory — easy on the eyes",
  palette: {
    background: { hex: "#1a0f15", alpha: 1 },
    midground: { hex: "#ffd4e1", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(249, 168, 212, 0.3)",
    noiseOpacity: 0.9,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Fraunces", Georgia, serif`,
    fontMono: `"DM Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=DM+Mono:wght@400;500&display=swap",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "1rem",
  },
};

/**
 * Same look as ``defaultTheme`` but with a larger root font size, looser
 * line-height, and ``spacious`` density so every rem-based size in the
 * dashboard scales up. For users who find the default 15px UI too dense.
 */
export const defaultLargeTheme: DashboardTheme = {
  name: "default-large",
  label: "Hermes Teal (Large)",
  description: "Hermes Teal with bigger fonts and roomier spacing",
  palette: defaultTheme.palette,
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    baseSize: "18px",
    lineHeight: "1.65",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    density: "spacious",
  },
};

/**
 * GBAutomation brand theme.
 *
 * Ports the public site's palette (gb-automation-landing/src/index.css) to
 * the dashboard: a light cream canvas, near-black text, terracotta accent.
 * Inter for body copy, Newsreader for display headings.
 *
 * Unlike every other preset this is a LIGHT theme — the palette triplet is
 * inverted (light background / dark midground). The DS `color-mix()` cascade
 * is symmetric so the derived shadcn tokens still resolve; `colorOverrides`
 * pins the exact brand hexes. `noiseOpacity` is dialled near-zero because the
 * Backdrop's `color-dodge` grain layer blows out on a light canvas.
 */
export const gbautomationTheme: DashboardTheme = {
  name: "gbautomation",
  label: "GBAutomation",
  description: "Cream canvas, terracotta accent — the GBAutomation brand",
  palette: {
    background: { hex: "#f3f1e7", alpha: 1 }, // --cream-bg
    midground: { hex: "#191919", alpha: 1 }, // --text-main
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(217, 119, 87, 0.5)", // --terracotta
    noiseOpacity: 0.1,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Inter", ${SYSTEM_SANS}`,
    fontMono: `"JetBrains Mono", ${SYSTEM_MONO}`,
    fontDisplay: `"Newsreader", Georgia, "Times New Roman", serif`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500;6..72,600&family=JetBrains+Mono:wght@400;500;700&display=swap",
    letterSpacing: "-0.005em",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0.5rem",
  },
  colorOverrides: {
    // Panels / surfaces — --cream-panel.
    card: "#e6e4d9",
    cardForeground: "#191919",
    popover: "#e6e4d9",
    popoverForeground: "#191919",
    secondary: "#e6e4d9",
    secondaryForeground: "#191919",
    muted: "#e9e7dc",
    mutedForeground: "#5c5c5c", // --text-muted
    // Hover/active chrome — a touch warmer than the panel.
    accent: "#e0ddcf",
    accentForeground: "#191919",
    // Hairlines — --border.
    border: "#d6d4c8",
    input: "#d6d4c8",
    // Terracotta focus ring, mirroring the site's input :focus state.
    ring: "#d97757",
    destructive: "#c0392b",
    destructiveForeground: "#ffffff",
    success: "#4f7a52",
    warning: "#d97757", // --terracotta
  },
  customCSS: `
    /* Newsreader serif for display headings — mirrors the GBAutomation site. */
    h1, h2, h3 {
      font-family: var(--theme-font-display);
      letter-spacing: -0.01em;
    }
    /* Warm scrollbar to match gb-automation-landing. */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-thumb {
      background: #d1cec3;
      border-radius: 4px;
    }
    ::-webkit-scrollbar-thumb:hover { background: #b0ada5; }
  `,
};

export const gbautomationFullTheme: DashboardTheme = {
  name: "gbautomation-full",
  label: "GBAutomation Full",
  description: "Full GBautomation operations skin with Aura-derived glass chrome",
  palette: gbautomationTheme.palette,
  typography: {
    ...gbautomationTheme.typography,
    fontMono: SYSTEM_MONO,
    letterSpacing: "0",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0.5rem",
    density: "comfortable",
  },
  colorOverrides: {
    ...gbautomationTheme.colorOverrides,
    primary: "#191919",
    primaryForeground: "#f3f1e7",
    card: "#e6e4d9",
    muted: "#e9e7dc",
    accent: "#ffffff",
  },
  componentStyles: {
    card: {
      background:
        "linear-gradient(180deg, rgba(255,255,255,.68) 0%, rgba(230,228,217,.64) 100%)",
      boxShadow: "0 24px 80px -58px rgba(25,25,25,.58)",
    },
    sidebar: {
      background: "rgba(243,241,231,.94)",
    },
    header: {
      background: "rgba(243,241,231,.92)",
    },
  },
  customCSS: `
    :root[data-hermes-theme="gbautomation-full"] body {
      background: #f3f1e7;
      color: #191919;
    }

    :root[data-hermes-theme="gbautomation-full"] #root > div {
      background: #f3f1e7 !important;
      color: #191919;
      text-transform: none;
    }

    :root[data-hermes-theme="gbautomation-full"] #root > div > div[aria-hidden]:nth-of-type(1) {
      background: #f3f1e7 !important;
      mix-blend-mode: normal !important;
    }

    :root[data-hermes-theme="gbautomation-full"] #root > div > div[aria-hidden]:nth-of-type(2) {
      mix-blend-mode: normal !important;
      opacity: .045 !important;
    }

    :root[data-hermes-theme="gbautomation-full"] #root > div > div[aria-hidden]:nth-of-type(2) img {
      filter: grayscale(1) !important;
      opacity: .72;
    }

    :root[data-hermes-theme="gbautomation-full"] #root > div > div[aria-hidden]:nth-of-type(3) {
      mix-blend-mode: multiply !important;
      opacity: .18 !important;
    }

    :root[data-hermes-theme="gbautomation-full"] #root > div::before,
    :root[data-hermes-theme="gbautomation-full"] #root > div::after {
      content: "";
      position: fixed;
      z-index: 0;
      pointer-events: none;
      width: 42vw;
      height: 42vw;
      border-radius: 999px;
      filter: blur(120px);
      opacity: .38;
    }

    :root[data-hermes-theme="gbautomation-full"] #root > div::before {
      top: -20vw;
      left: -18vw;
      background: rgba(217, 119, 87, .18);
    }

    :root[data-hermes-theme="gbautomation-full"] #root > div::after {
      right: -18vw;
      bottom: -20vw;
      background: rgba(183, 95, 67, .14);
    }

    :root[data-hermes-theme="gbautomation-full"] h1,
    :root[data-hermes-theme="gbautomation-full"] h2,
    :root[data-hermes-theme="gbautomation-full"] h3,
    :root[data-hermes-theme="gbautomation-full"] .font-display {
      font-family: var(--theme-font-display);
      font-weight: 500;
      letter-spacing: 0;
    }

    :root[data-hermes-theme="gbautomation-full"] #app-sidebar {
      border-right-color: rgba(214, 212, 200, .84);
      box-shadow: 28px 0 80px -72px rgba(25, 25, 25, .62);
    }

    :root[data-hermes-theme="gbautomation-full"] #app-sidebar nav {
      border-top-color: rgba(214, 212, 200, .68);
    }

    :root[data-hermes-theme="gbautomation-full"] #app-sidebar a,
    :root[data-hermes-theme="gbautomation-full"] #app-sidebar button {
      font-family: var(--theme-font-sans);
      letter-spacing: .08em;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-sidebar-section {
      margin-top: .35rem;
      border-top-color: rgba(214, 212, 200, .84);
      background: linear-gradient(180deg, rgba(255,255,255,.34), rgba(230,228,217,.28));
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-sidebar-section > span {
      color: #d97757;
      opacity: 1;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-surface {
      position: relative;
      overflow: hidden;
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
      border: 1px solid rgba(214, 212, 200, .86);
      border-radius: 8px;
      background:
        linear-gradient(135deg, rgba(255,255,255,.74), rgba(230,228,217,.6)),
        radial-gradient(circle at 12% 0%, rgba(217,119,87,.16), transparent 34%);
      box-shadow: 0 28px 90px -70px rgba(25, 25, 25, .7);
      padding: 1rem;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-header {
      display: flex;
      min-width: 0;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      border-bottom: 1px solid rgba(214, 212, 200, .78);
      padding-bottom: .9rem;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-eyebrow {
      margin: 0 0 .35rem;
      color: #d97757 !important;
      font-size: .68rem;
      font-weight: 700;
      letter-spacing: .14em;
      text-transform: uppercase;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-header h2 {
      margin: 0;
      color: #191919;
      font-size: clamp(1.6rem, 3vw, 2.45rem);
      line-height: 1;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-header p:not(.gbauto-eyebrow) {
      margin: .55rem 0 0;
      max-width: 46rem;
      color: #5c5c5c;
      font-size: .9rem;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-theme-badge {
      display: inline-flex;
      flex-shrink: 0;
      align-items: center;
      gap: .35rem;
      background: rgba(255,255,255,.55);
      color: #191919;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: .65rem;
      padding-top: .9rem;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-card {
      min-height: 8.25rem;
      min-width: 0;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: .7rem;
      border: 1px solid rgba(214, 212, 200, .88);
      border-radius: 8px;
      background: rgba(255,255,255,.5);
      color: #191919;
      padding: .85rem;
      text-decoration: none;
      transition: border-color .18s ease, box-shadow .18s ease, transform .18s ease, background .18s ease;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-card:hover {
      transform: translateY(-2px);
      border-color: rgba(217, 119, 87, .68);
      background: rgba(255,255,255,.72);
      box-shadow: 0 18px 48px -36px rgba(217, 119, 87, .55);
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-icon-badge {
      width: 2.05rem;
      height: 2.05rem;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid rgba(217, 119, 87, .24);
      border-radius: 8px;
      background: rgba(217, 119, 87, .1);
      color: #d97757;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-card-title {
      display: block;
      max-width: 100%;
      color: #191919;
      font-weight: 700;
      font-size: .85rem;
      letter-spacing: .08em;
      text-transform: uppercase;
      white-space: normal;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-card-description {
      display: block;
      margin-top: .35rem;
      max-width: 100%;
      color: #5c5c5c;
      font-size: .72rem;
      line-height: 1.45;
      overflow-wrap: anywhere;
      white-space: normal;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-card-action {
      color: #8c8a84;
      transition: color .18s ease, transform .18s ease;
    }

    :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-card:hover .gbauto-card-action {
      color: #d97757;
      transform: translateX(2px);
    }

    :root[data-hermes-theme="gbautomation-full"] input,
    :root[data-hermes-theme="gbautomation-full"] textarea,
    :root[data-hermes-theme="gbautomation-full"] select {
      border-color: #d6d4c8;
      background: rgba(255,255,255,.55);
      color: #191919;
    }

    :root[data-hermes-theme="gbautomation-full"] input:focus,
    :root[data-hermes-theme="gbautomation-full"] textarea:focus {
      border-color: #d97757;
      box-shadow: 0 0 0 1px rgba(217,119,87,.45);
    }

    :root[data-hermes-theme="gbautomation-full"] ::selection {
      background: rgba(217, 119, 87, .25);
    }

    :root[data-hermes-theme="gbautomation-full"] ::-webkit-scrollbar {
      width: 8px;
      height: 8px;
    }

    :root[data-hermes-theme="gbautomation-full"] ::-webkit-scrollbar-thumb {
      background: #d1cec3;
      border-radius: 4px;
    }

    :root[data-hermes-theme="gbautomation-full"] ::-webkit-scrollbar-thumb:hover {
      background: #b0ada5;
    }

    @media (max-width: 1024px) {
      :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }

    @media (max-width: 640px) {
      :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-surface {
        width: calc(100dvw - 1.5rem);
        max-width: calc(100dvw - 1.5rem);
      }

      :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-header {
        flex-direction: column;
      }

      :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-grid {
        grid-template-columns: 1fr;
      }

      :root[data-hermes-theme="gbautomation-full"] .gbauto-card-description,
      :root[data-hermes-theme="gbautomation-full"] .gbauto-ops-header p:not(.gbauto-eyebrow) {
        width: min(18rem, calc(100dvw - 4.5rem));
        max-width: min(18rem, calc(100dvw - 4.5rem));
      }
    }
  `,
};

export const BUILTIN_THEMES: Record<string, DashboardTheme> = {
  gbautomation: gbautomationTheme,
  "gbautomation-full": gbautomationFullTheme,
  default: defaultTheme,
  "default-large": defaultLargeTheme,
  midnight: midnightTheme,
  ember: emberTheme,
  mono: monoTheme,
  cyberpunk: cyberpunkTheme,
  rose: roseTheme,
};
