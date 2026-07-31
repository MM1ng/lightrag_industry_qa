"""Streamlit UI theme — design tokens, CSS injection, responsive layout.

No business state, no Runtime access, no QueryResult handling.

Selectors verified against Streamlit 1.60 frontend bundle:
- className / data-testid: stApp, stAppViewContainer, stMain,
  stMainBlockContainer, stBottom, stBottomBlockContainer,
  stChatMessage, stChatInput, stHeader, stColumn, stVerticalBlock
- container/widget key becomes class st-key-<key>
- no data-theme attribute; dark tokens use prefers-color-scheme
"""

from __future__ import annotations

import streamlit as st


def inject_theme_css() -> None:
    """Inject the design-system CSS once after st.set_page_config."""
    st.markdown(f"<style>{_build_css()}</style>", unsafe_allow_html=True)


def _build_css() -> str:
    return f"{_LIGHT_TOKENS}\n{_DARK_TOKENS}\n{_OVERRIDES}"


_LIGHT_TOKENS = """
:root {
  --bg-page: #F5F5F7;
  --bg-surface: #FFFFFF;
  --text-primary: #1D1D1F;
  --text-secondary: #6E6E73;
  --text-tertiary: #AEAEB2;
  --border-light: #E5E5EA;
  --border-medium: #D1D1D6;
  --brand-accent: #2563EB;
  --brand-accent-subtle: rgba(37, 99, 235, 0.08);
  --color-success: #30B358;
  --color-warning: #F59E0B;
  --color-error: #EF4444;
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.06);
  --max-content-width: 760px;
  --font-system: "Segoe UI", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: "Cascadia Code", "Consolas", "Courier New", monospace;
}
"""

_DARK_TOKENS = """
/* Streamlit 1.60 has no stable data-theme attribute in the DOM.
   Use system dark preference as a best-effort dark token set. */
@media (prefers-color-scheme: dark) {
  :root {
    --bg-page: #1C1C1E;
    --bg-surface: #2C2C2E;
    --text-primary: #F5F5F7;
    --text-secondary: #AEAEB2;
    --text-tertiary: #6E6E73;
    --border-light: #3A3A3C;
    --border-medium: #545458;
    --brand-accent: #3B82F6;
    --brand-accent-subtle: rgba(59, 130, 246, 0.14);
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.28);
    --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.35);
  }
}
"""

_OVERRIDES = """
/* ------------------------------------------------------------------ */
/* Global page shell                                                    */
/* ------------------------------------------------------------------ */
.stApp,
[data-testid="stApp"] {
  background: var(--bg-page) !important;
  color: var(--text-primary);
  font-family: var(--font-system);
  overflow-x: hidden;
}

[data-testid="stAppViewContainer"],
.stAppViewContainer {
  background: var(--bg-page) !important;
  color: var(--text-primary);
  font-family: var(--font-system);
}

[data-testid="stHeader"] {
  background: color-mix(in srgb, var(--bg-page) 88%, transparent) !important;
}

.stMain,
[data-testid="stMain"] {
  background: var(--bg-page) !important;
  font-family: var(--font-system);
  color: var(--text-primary);
}

.stMainBlockContainer.block-container,
[data-testid="stMainBlockContainer"] {
  background: var(--bg-page) !important;
  padding-top: 1rem;
  padding-bottom: 5.5rem;
}

/* Dense default vertical rhythm inside main content */
.stMainBlockContainer [data-testid="stVerticalBlock"] {
  gap: 0.55rem;
}

/* ------------------------------------------------------------------ */
/* Status strip (keyed container)                                       */
/* ------------------------------------------------------------------ */
.st-key-status-bar {
  max-width: var(--max-content-width);
  margin: 0 auto 0.35rem auto;
  padding: 0.55rem 0.9rem;
  background: var(--bg-surface);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
}

.st-key-status-bar [data-testid="stCaptionContainer"],
.st-key-status-bar [data-testid="stCaptionContainer"] p {
  color: var(--text-secondary) !important;
  font-size: 0.84rem;
  line-height: 1.35;
  margin-bottom: 0 !important;
}

/* ------------------------------------------------------------------ */
/* QA shell: constrain chat column only (graph stays free width)        */
/* ------------------------------------------------------------------ */
.st-key-qa-shell {
  max-width: var(--max-content-width);
  margin-left: auto;
  margin-right: auto;
}

.st-key-qa-toolbar {
  margin-bottom: 0.35rem;
}

.st-key-qa-toolbar [data-testid="stSelectbox"] {
  max-width: 100%;
}

.st-key-qa-toolbar [data-baseweb="select"] > div {
  background: var(--bg-surface);
  border-color: var(--border-light);
  border-radius: var(--radius-md);
  min-height: 2.5rem;
}

.st-key-qa-toolbar .stButton > button {
  background: var(--bg-surface);
  border: 1px solid var(--border-medium);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-weight: 500;
  min-height: 2.5rem;
  box-shadow: var(--shadow-sm);
}

.st-key-qa-toolbar .stButton > button:hover {
  border-color: var(--brand-accent);
  color: var(--brand-accent);
  background: var(--brand-accent-subtle);
}

/* ------------------------------------------------------------------ */
/* Empty state + suggestion chips                                       */
/* ------------------------------------------------------------------ */
.st-key-empty-state {
  max-width: var(--max-content-width);
  margin: 0.75rem auto 0.25rem auto;
  padding: 1.25rem 0.25rem 0.5rem;
}

.st-key-empty-state h3 {
  text-align: center;
  color: var(--text-primary);
  font-weight: 650;
  letter-spacing: 0;
  margin-bottom: 0.25rem;
}

.st-key-empty-state [data-testid="stCaptionContainer"] {
  text-align: center;
  color: var(--text-secondary) !important;
  margin-bottom: 0.85rem;
}

.st-key-empty-state .stButton > button {
  background: var(--bg-surface) !important;
  border: 1px solid var(--border-light) !important;
  border-radius: var(--radius-md) !important;
  color: var(--text-primary) !important;
  box-shadow: var(--shadow-sm);
  min-height: 3.1rem;
  padding: 0.75rem 1rem !important;
  white-space: normal;
  word-break: break-word;
  text-align: left !important;
  justify-content: flex-start !important;
  font-weight: 500;
  line-height: 1.35;
}

.st-key-empty-state .stButton > button:hover {
  border-color: var(--brand-accent);
  background: var(--brand-accent-subtle);
  color: var(--brand-accent);
  box-shadow: var(--shadow-md);
}

.st-key-empty-state .stButton > button:focus-visible {
  outline: 2px solid var(--brand-accent);
  outline-offset: 2px;
}

/* ------------------------------------------------------------------ */
/* Chat messages                                                        */
/* ------------------------------------------------------------------ */
.stChatMessage,
[data-testid="stChatMessage"] {
  max-width: var(--max-content-width);
  margin-left: auto !important;
  margin-right: auto !important;
  margin-bottom: 0.65rem;
  background: var(--bg-surface) !important;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 0.85rem 1rem;
}

[data-testid="stChatMessageContent"] {
  overflow-wrap: break-word;
  word-break: break-word;
  color: var(--text-primary);
}

[data-testid="stCaptionContainer"] {
  color: var(--text-secondary);
}

/* Citations expander stays readable inside chat width */
.st-key-qa-shell [data-testid="stExpander"],
[data-testid="stChatMessage"] [data-testid="stExpander"] {
  max-width: 100%;
  background: transparent;
  border-color: var(--border-light);
  border-radius: var(--radius-sm);
}

/* ------------------------------------------------------------------ */
/* Bottom chat dock                                                     */
/* ------------------------------------------------------------------ */
[data-testid="stBottom"] {
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--bg-page) 0%, transparent) 0%,
    var(--bg-page) 28%
  ) !important;
  padding-top: 0.5rem;
  border-top: 1px solid var(--border-light);
}

[data-testid="stBottomBlockContainer"] {
  max-width: calc(var(--max-content-width) + 2rem);
  margin-left: auto;
  margin-right: auto;
  padding-bottom: 0.85rem;
  background: transparent !important;
}

[data-testid="stChatInput"] {
  max-width: var(--max-content-width);
  margin-left: auto;
  margin-right: auto;
  background: var(--bg-surface) !important;
  border: 1px solid var(--border-medium) !important;
  border-radius: var(--radius-lg) !important;
  box-shadow: var(--shadow-md);
}

[data-testid="stChatInput"] textarea {
  min-height: 56px;
  color: var(--text-primary);
  font-family: var(--font-system);
}

/* ------------------------------------------------------------------ */
/* Tabs + general widgets (shared look, graph-safe)                     */
/* ------------------------------------------------------------------ */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
  gap: 0.25rem;
  border-bottom: 1px solid var(--border-light);
  background: transparent;
}

[data-testid="stTabs"] button[data-baseweb="tab"] {
  color: var(--text-secondary);
  font-weight: 500;
}

[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] {
  color: var(--brand-accent);
  font-weight: 600;
}

[data-testid="stMetric"] {
  background: var(--bg-surface);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 0.65rem 0.8rem;
  box-shadow: var(--shadow-sm);
}

.stButton button {
  white-space: normal;
  word-break: break-word;
  font-family: var(--font-system);
}

/* Long ids / filenames should wrap, not force horizontal scroll */
.stApp p, .stApp span, .stApp code, .stApp pre {
  overflow-wrap: anywhere;
  word-break: break-word;
}

/* ------------------------------------------------------------------ */
/* Responsive                                                           */
/* ------------------------------------------------------------------ */
@media (max-width: 1366px) {
  :root {
    --max-content-width: 720px;
  }
}

@media (max-width: 768px) {
  :root {
    --max-content-width: 100%;
  }

  .stMainBlockContainer.block-container,
  [data-testid="stMainBlockContainer"] {
    padding-left: 0.85rem;
    padding-right: 0.85rem;
    padding-top: 0.75rem;
  }

  .st-key-status-bar,
  .st-key-qa-shell,
  .st-key-empty-state,
  .stChatMessage,
  [data-testid="stChatMessage"],
  [data-testid="stChatInput"],
  [data-testid="stBottomBlockContainer"] {
    max-width: 100%;
  }

  .st-key-status-bar {
    margin-left: 0;
    margin-right: 0;
  }

  .st-key-empty-state .stButton > button {
    text-align: left;
  }
}
"""
