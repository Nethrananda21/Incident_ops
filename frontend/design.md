---
name: Operational Incident Console
colors:
  surface: '#ffffff'
  surface-dim: '#f0f2f5'
  surface-bright: '#ffffff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f8f9fb'
  surface-container: '#eef0f3'
  surface-container-high: '#e5e7eb'
  surface-container-highest: '#d1d5db'
  on-surface: '#0f172a'
  on-surface-variant: '#374151'
  inverse-surface: '#0f172a'
  inverse-on-surface: '#f1f5f9'
  outline: '#d1d5db'
  outline-variant: '#e5e7eb'
  surface-tint: '#2563eb'
  primary: '#2563eb'
  on-primary: '#ffffff'
  primary-container: '#dbeafe'
  on-primary-container: '#1d4ed8'
  inverse-primary: '#93c5fd'
  secondary: '#0f172a'
  on-secondary: '#f1f5f9'
  secondary-container: '#1e293b'
  on-secondary-container: '#94a3b8'
  tertiary: '#0891b2'
  on-tertiary: '#ffffff'
  tertiary-container: '#cffafe'
  on-tertiary-container: '#155e75'
  error: '#dc2626'
  on-error: '#ffffff'
  error-container: '#fee2e2'
  on-error-container: '#991b1b'
  primary-fixed: '#dbeafe'
  primary-fixed-dim: '#bfdbfe'
  on-primary-fixed: '#1e3a8a'
  on-primary-fixed-variant: '#1d4ed8'
  secondary-fixed: '#e2e8f0'
  secondary-fixed-dim: '#cbd5e1'
  on-secondary-fixed: '#0f172a'
  on-secondary-fixed-variant: '#475569'
  tertiary-fixed: '#cffafe'
  tertiary-fixed-dim: '#a5f3fc'
  on-tertiary-fixed: '#164e63'
  on-tertiary-fixed-variant: '#0e7490'
  background: '#f0f2f5'
  on-background: '#0f172a'
  surface-variant: '#f8f9fb'
  success: '#16a34a'
  success-container: '#dcfce7'
  warning: '#d97706'
  warning-container: '#fef3c7'
  critical: '#dc2626'
  critical-container: '#fee2e2'
  sidebar-background: '#0f172a'
  sidebar-text: '#94a3b8'
  sidebar-border: '#1e293b'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 30px
    fontWeight: '700'
    lineHeight: '1'
    letterSpacing: -0.03em
  h1:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '700'
    lineHeight: '1.25'
    letterSpacing: -0.02em
  h2:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: '700'
    lineHeight: '1.3'
    letterSpacing: -0.01em
  data-mono:
    fontFamily: IBM Plex Mono
    fontSize: 12px
    fontWeight: '600'
    lineHeight: '1.4'
    letterSpacing: 0.04em
  body-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: '1.5'
    letterSpacing: 0em
  body-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: '1.5'
    letterSpacing: 0em
  label-caps:
    fontFamily: Inter
    fontSize: 10px
    fontWeight: '600'
    lineHeight: '1'
    letterSpacing: 0.08em
  caption:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '500'
    lineHeight: '1.4'
    letterSpacing: 0.04em
spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  gutter: 12px
  margin-page: 24px
---

## Brand & Style

The design system is an operational incident command console for high-volume routing, escalation, privacy, and intelligence workflows. It should feel calm, fast, and credible: a live NOC surface where analysts can scan events, compare confidence signals, and act without decorative distraction.

The emotional response should be one of controlled urgency. The interface uses a crisp light workspace for readability, a dark slate navigation rail for orientation, compact tables for density, and blue accents for active routing decisions. Status color is meaningful and reserved for system health, confidence, SLA risk, and escalation state.

## Colors

The color strategy is a light neutral foundation with a dark command sidebar and a narrow set of operational status colors. White surfaces sit on a cool gray app background, with borders doing most of the structural work.

- **Primary Action:** #2563eb is used for active navigation, primary buttons, selected rows, routing confidence, pipeline progress, and links to decision details.
- **Workspace Surfaces:** #ffffff, #f8f9fb, and #eef0f3 separate cards, table headers, filters, bars, and low-emphasis panels.
- **Text Hierarchy:** #0f172a is reserved for titles and key values; #374151 supports readable body text; #6b7280 handles metadata and labels.
- **Operational States:** #16a34a indicates healthy, solved, cached, or passing states. #d97706 marks warning, elevated attention, or pending review. #dc2626 is reserved for critical SLA, escalation, failed health, low confidence, and privacy risk.
- **Navigation:** #0f172a anchors the sidebar, with #94a3b8 for inactive links and #f1f5f9 for active or hover labels.

## Typography

The system uses **Inter** as the primary interface typeface. It keeps dense operational text legible at small sizes and supports the product's quiet, professional tone.

**IBM Plex Mono** is used for ticket IDs, timestamps, confidence values, API routes, clocks, compact metrics, pipeline indices, and technical readouts. The monospaced face should signal machine-readable evidence rather than brand personality.

Labels use uppercase Inter at 10-11px with generous tracking. KPI values are large, tight, and numeric-first, usually 30px Inter Bold or compact IBM Plex Mono when alignment matters.

## Layout & Spacing

The layout is a fixed operations shell: a 56px topbar, 200px left sidebar, and a scrollable main canvas with 24px page padding. Pages use compact grids with 12px gaps and dense table rows so analysts can keep many signals in view.

Cards, KPI panels, filter bars, modals, and route pipeline nodes use consistent 8px radii and 1px borders. Page sections should avoid marketing-style whitespace; spacing should create scan paths, not atmosphere. Data tables and list panels should preserve stable column widths and ellipsis behavior for long ticket descriptions.

## Elevation & Depth

Depth is restrained and functional. Cards use a subtle 0 1px 3px shadow, topbars use a light 0 1px 4px shadow, and modals or drawers use stronger shadows only when they must sit above the workflow.

Hierarchy is established through:
1. **Surface Contrast:** White cards against the cool gray workspace.
2. **Borders:** #e5e7eb and #d1d5db define containers, rows, filters, and controls.
3. **State Color:** Blue, green, amber, and red communicate active decisions and operational risk.
4. **Density:** Tables, KPI grids, and pipeline nodes communicate priority through placement and compact scale.

## Shapes

The shape language is practical and slightly softened. Cards and panels use 8px radius, buttons and inputs use 6px radius, badges use 4px radius, and pills or live indicators may use fully rounded corners.

Sharp geometry is not the goal; predictable containment is. Roundness should remain restrained so the interface reads as an analytical tool rather than a consumer dashboard.

## Components

### Buttons
Primary buttons are solid #2563eb with white text and a darker #1d4ed8 hover state. Ghost buttons are white with #d1d5db borders and slate text. Approve and reject actions use state colors only on border/text until hover, preserving urgency for confirmed states.

### Cards
Cards use #ffffff surfaces, 1px #e5e7eb borders, 8px radius, 16px padding, and a subtle shadow. Card titles are uppercase, 12px, semibold, and muted. Avoid nesting cards inside cards unless the inner element is an explicit repeated item or pipeline node.

### Input Fields
Inputs and selects use white backgrounds, 1.5px #d1d5db borders, 6px radius, 8px 12px padding, and Inter 14px. Focus state uses #2563eb border with a soft blue focus ring.

### Active/Critical States
- **Active State:** Use #2563eb for selected navigation, selected rows, active chips, pipeline completion, and selected filters.
- **Warning State:** Use #d97706 with #fef3c7 backgrounds for pending review, elevated escalation probability, and human-review highlights.
- **Critical State:** Use #dc2626 with #fee2e2 or #fff5f5 backgrounds for SLA danger, failed health, privacy risk, escalation required, and low confidence.
- **Success State:** Use #16a34a with #dcfce7 or #f0fdf4 backgrounds for solved, healthy, cached, and approved outcomes.

### Data Tables
Tables are central to the experience. Headers use #f8f9fb backgrounds, uppercase 11px labels, and 1.5px bottom borders. Body rows use 13px text with 10px 12px padding and 1px row dividers. Hover states use a pale blue tint (#f0f7ff) to indicate inspectable rows.

### Navigation
The sidebar is dark slate with muted inactive labels and a 3px blue left border for the active item. Navigation sections are uppercase, 10px, and widely tracked. The topbar pairs the product identity with the current page title, live status pill, and monospaced clock.

### Motion
Motion should be short and diagnostic. Use 120-250ms transitions for hover, drawer, modal, and row states. Data loading may fade or slide in slightly. Confidence bars and route pipelines can animate progress, but motion should remain mechanical and reduce gracefully when reduced-motion is enabled.
