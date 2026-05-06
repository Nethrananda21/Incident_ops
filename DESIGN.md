# Design System: Incident AI Operations Console
**Project ID:** local-docker-mvp

## 1. Visual Theme & Atmosphere
Dense, operational, and enterprise-grade with a command-center feel. The interface favors scan speed over decoration: compact data panels, live event rows, precise status chips, and clear navigation for ticket classification, routing, privacy audit, escalation, and RAG resolution workflows.

## 2. Color Palette & Roles
- Graphite Control Surface (#18202B): Used for the persistent navigation rail and high-contrast operational framing.
- Porcelain Workspace (#F5F7FA): Used for the main application background to keep dense data readable.
- Clean Panel White (#FFFFFF): Used for tables, forms, metric panels, and work surfaces.
- Signal Teal (#0F766E): Used for primary routing actions, active navigation, and healthy service states.
- Focus Cyan (#0284C7): Used for live stream indicators and retrieval/RAG emphasis.
- Escalation Amber (#B45309): Used for uncertain confidence, human review, and warnings.
- Risk Red (#B42318): Used for privacy risk, failed actions, or low verifier confidence.
- Quiet Slate Text (#475467): Used for secondary metadata and table supporting copy.

## 3. Typography Rules
Use a system sans-serif stack with crisp numeric rendering. Section titles are compact and semibold, metric values are large but not hero-scale, and table text stays tight for repeated operational scanning. Letter spacing remains neutral.

## 4. Component Stylings
* **Buttons:** Squared enterprise buttons with subtle six-pixel corners. Primary buttons use Signal Teal; secondary controls use quiet gray fills; destructive or escalation actions use Amber or Red.
* **Cards/Containers:** Flat white panels with fine gray borders and minimal shadow. Cards are reserved for metric units and repeated records, not page sections inside page sections.
* **Inputs/Forms:** White fields with restrained gray strokes, compact height, and focus rings in Signal Teal.
* **Tables/Feeds:** Row-first information density with category chips, confidence bars, and monospace ticket identifiers.

## 5. Layout Principles
Use a persistent left navigation rail, a top service-status bar, and page-level workspaces. The dashboard combines live metrics and recent activity; streaming tickets use a two-column operational feed; routing is a focused workbench; audit/escalation/knowledge pages are table-first.
