---
name: Thermal Precision AI
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#43474d'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#74777e'
  outline-variant: '#c4c6ce'
  surface-tint: '#476080'
  primary: '#00162d'
  on-primary: '#ffffff'
  primary-container: '#0f2b48'
  on-primary-container: '#7a93b5'
  inverse-primary: '#afc8ed'
  secondary: '#a73a00'
  on-secondary: '#ffffff'
  secondary-container: '#fd651e'
  on-secondary-container: '#571a00'
  tertiary: '#001727'
  on-tertiary: '#ffffff'
  tertiary-container: '#002c47'
  on-tertiary-container: '#3197dc'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d2e4ff'
  primary-fixed-dim: '#afc8ed'
  on-primary-fixed: '#001c37'
  on-primary-fixed-variant: '#2f4867'
  secondary-fixed: '#ffdbce'
  secondary-fixed-dim: '#ffb599'
  on-secondary-fixed: '#370e00'
  on-secondary-fixed-variant: '#7f2b00'
  tertiary-fixed: '#cce5ff'
  tertiary-fixed-dim: '#93ccff'
  on-tertiary-fixed: '#001d31'
  on-tertiary-fixed-variant: '#004b73'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-xl:
    fontFamily: Noto Sans
    fontSize: 56px
    fontWeight: '700'
    lineHeight: 64px
    letterSpacing: -0.025em
  headline-xl-mobile:
    fontFamily: Noto Sans
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 44px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Noto Sans
    fontSize: 40px
    fontWeight: '700'
    lineHeight: 48px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Noto Sans
    fontSize: 28px
    fontWeight: '700'
    lineHeight: 36px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Noto Sans
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Noto Sans
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 30px
    letterSpacing: -0.005em
  title-md:
    fontFamily: Noto Sans
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 26px
    letterSpacing: 0em
  body-lg:
    fontFamily: Noto Sans
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
    letterSpacing: -0.005em
  body-md:
    fontFamily: Noto Sans
    fontSize: 15px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: 0em
  body-sm:
    fontFamily: Noto Sans
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  label-md:
    fontFamily: Noto Sans
    fontSize: 13px
    fontWeight: '600'
    lineHeight: 18px
    letterSpacing: 0.02em
  label-sm:
    fontFamily: Noto Sans
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.05em
  metric-display:
    fontFamily: Noto Sans
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 52px
    letterSpacing: -0.03em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1.5rem
  gutter-mobile: 1rem
  margin: 2rem
  margin-mobile: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.5rem
---

## Brand & Style

This design system establishes an architectural, high-trust visual language tailored for commercial facility directors, building automation engineers, and enterprise operations executives. The interface balances high-performance edge hardware with silent, autonomous intelligence. 

The aesthetic is Modern Corporate Precision: crisp structural boundaries, expansive whitespace, calibrated informational rhythm, and deep chromatic control. It avoids decorative clutter in favor of legible metrics, engineering clarity, and responsive kinetic stability. The experience communicates reliability, enterprise-grade uptime, thermal mastery, and tangible energy optimization.

## Colors

The palette establishes thermal balance through contrasting cooling tones and measured kinetic warmth:

- **Primary (`#0F2B48`)**: Deep Marine Blue. Anchors the identity in computational precision, structural reliability, and cool stability. Used for critical brand statements, high-tier headlines, and structural boundaries.
- **Secondary (`#EA580C`)**: Thermal Orange. Represents active heating management, real-time demand, peak load efficiency, and primary calls to action. Used purposefully to highlight thermal equilibrium and key interaction points.
- **Tertiary (`#0284C7`)**: Atmospheric Cyan. Signifies active airflow, cooling efficiency, and predictive edge compute states.
- **Neutral Palette**: 
  - Background Base: `#FFFFFF` (pure crisp canvas)
  - Surface Neutral Low: `#F8FAFC` (ambient background panels and soft page tiering)
  - Surface Neutral Mid: `#F1F5F9` (card tracks, control boundaries, table headers)
  - Border Subdued: `#E2E8F0` (thin structural architectural line work)
  - Text Primary: `#0F172A` (deep slate text with maximum contrast)
  - Text Secondary: `#475569` (supporting technical specifications and metadata)
  - Text Muted: `#94A3B8` (inactive states, telemetry units, secondary grid ticks)

## Typography

Typography prioritizes functional legibility, global CJK compatibility, and structured data hierarchy via Noto Sans. 

All titles utilize tighter letter tracking to preserve density and visual authority at large scales. Body text operates with generous line heights to facilitate comfortable scanning of operational specifications and technical overviews. Numerical data and real-time readouts rely on tabular figures (`tnum`) to maintain vertical alignment within metrics cards, tables, and sensor telemetry monitors.

## Layout & Spacing

The layout is built around a 12-column responsive fluid grid with maximum bounded containers for enterprise desktop viewports:

- **Desktop (1200px and up)**: 12 columns, 24px (`1.5rem`) gutters, safe section margins scaling up to a max-width container of `1280px`. Vertical section padding ranges between `5rem` and `7.5rem` to enforce visual breathing room.
- **Tablet (768px – 1199px)**: 8 columns, 20px gutters, 32px lateral margins.
- **Mobile (up to 767px)**: 4 columns, 16px (`1rem`) gutters, 16px lateral margins. Cards and metrics stacks reflow vertically to preserve legibility.

All vertical stacks and internal component distances adhere to a strict base-8 rhythmic scale. Micro-spacing (labels, icon pairings) relies on 4px and 8px units, while macro layout spacing between functional card blocks uses 24px and 40px offsets.

## Elevation & Depth

Visual hierarchy combines tonal layering with deep-spectrum ambient diffusion:

- **Level 0 (Base Canvas)**: Pure white (`#FFFFFF`) or tinted neutral canvas (`#F8FAFC`). Flat with no shadow.
- **Level 1 (Card & Module Resting)**: Elevated cards utilize a dual-layer precision shadow tinted with the primary hue: `0 1px 3px rgba(15, 43, 72, 0.04), 0 10px 30px -5px rgba(15, 43, 72, 0.06)`. Accompanied by a 1px border in `#E2E8F0` or `#F1F5F9`.
- **Level 2 (Hover / Active Cards)**: `0 4px 6px -1px rgba(15, 43, 72, 0.04), 0 20px 40px -10px rgba(15, 43, 72, 0.10)`. The element lifts 2px along the Y-axis.
- **Level 3 (Sticky Navigation / Flyout Overlays)**: `0 20px 25px -5px rgba(15, 43, 72, 0.08), 0 8px 10px -6px rgba(15, 43, 72, 0.04)` combined with a frosted backdrop blur (`backdrop-filter: blur(12px); background: rgba(255, 255, 255, 0.88)`).

No pure black (`rgba(0, 0, 0, ...)`) shadows are permitted; all depth anchors to `#0F2B48` to preserve atmospheric coolness.

## Shapes

The interface balances organic comfort with technological order:

- **Primary Cards & Containers**: Feature modern `1rem` (16px, `rounded-2xl`) corners, softening data density and creating an approachable enterprise feel.
- **Interactive Controls (Buttons, Inputs, Selectors)**: Calibrated at `0.5rem` (8px) for structural sharpness and tactical feedback.
- **System Tags, Badges & Micro-pills**: Fully rounded pill shapes (`9999px`) to visually separate continuous status monitors from structural boundaries.

## Components

### Buttons
- **Primary CTA**: Background `#EA580C`, foreground `#FFFFFF`, font weight 600, padding `0.75rem 1.75rem`, border-radius `0.5rem`. Subtle warm glow on hover (`0 8px 20px -4px rgba(234, 88, 12, 0.35)`), active depression scale `0.98`.
- **Secondary Corporate Action**: Background `#0F2B48`, foreground `#FFFFFF`, border-radius `0.5rem`. Hover background `#173C63`.
- **Ghost / Outline**: Background transparent, border `1px solid #E2E8F0`, text `#0F2B48`. Hover background `#F8FAFC`, border color `#CBD5E1`.

### Edge AI Telemetry Cards
- Surface background `#FFFFFF`, border `1px solid #F1F5F9`, border-radius `1rem` (`rounded-2xl`). 
- Default shadow: `0 10px 30px -5px rgba(15, 43, 72, 0.06)`. Internal padding `1.75rem` (`space-lg` to `space-xl`).
- Dynamic header containing 1.5px thin stroke iconography, operational status indicator, primary metric readout, and delta comparison pill.

### Status Indicators & Badges
- Pill shape with subtle tint background (10% opacity) and solid text:
  - Optimal / Cooling: Background `#E0F2FE`, Text `#0369A1`
  - Active Heating / Rebalancing: Background `#FFEDD5`, Text `#C2410C`
  - Live Edge Node Active: Dot `#10B981` with pulse animation ring, text `#334155`

### Form Inputs & Selectors
- Height `44px`, border `1px solid #CBD5E1`, surface `#FFFFFF`, text `#0F172A`, border-radius `0.5rem`.
- Focus ring: `2px solid #0F2B48` with a 2px offset in `#FFFFFF`. Placeholder text `#94A3B8`.

### Line Iconography
- Consistent 1.5px line weight, square bounding box (20px or 24px), stroke caps and joins set to round. Toned in `#0F2B48` for key visual anchors or `#EA580C` for thermal alerts and active states.