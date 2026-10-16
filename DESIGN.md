---
name: Training Hub
description: A personal flight instrument for running with diabetes - glucose, training and the log on one time axis.
colors:
  bg: "#f4f2ed"
  surface: "#fbfaf7"
  border: "#dcd8cf"
  text: "#2a2824"
  muted: "#67635b"
  placeholder: "#6b665e"
  accent: "#1f7a6c"
  accent-text: "#ffffff"
  seg-bg: "#dcebe7"
  seg-text: "#17594f"
  glucose-good: "#195c2e"
  glucose-high: "#b08000"
  glucose-bad: "#cf4042"
  caution: "#8f540c"
  caution-bg: "#f6ead6"
  danger: "#b23a2e"
  danger-bg: "#f8e4df"
  chart-line: "#23887a"
  chart-band: "#e9e6df"
  dark-bg: "#1b1a18"
  dark-surface: "#232220"
  dark-border: "#36332f"
  dark-text: "#e8e4dc"
  dark-muted: "#a39e94"
  dark-accent: "#5fc2ad"
  dark-seg-bg: "#21413b"
  dark-glucose-good: "#79cd91"
  dark-glucose-high: "#f1c45e"
  dark-glucose-bad: "#e36460"
  dark-caution: "#e0a64a"
  dark-danger: "#ef7a6c"
typography:
  hero:
    fontFamily: "B612, system-ui, sans-serif"
    fontSize: "4.5rem"
    fontWeight: 700
    lineHeight: 1
  display:
    fontFamily: "B612, system-ui, sans-serif"
    fontSize: "2.25rem"
    fontWeight: 700
    lineHeight: 1.1
  heading:
    fontFamily: "B612, system-ui, sans-serif"
    fontSize: "1.4375rem"
    fontWeight: 700
    lineHeight: 1.25
  title:
    fontFamily: "B612, system-ui, sans-serif"
    fontSize: "1.1875rem"
    fontWeight: 700
    lineHeight: 1.25
  body:
    fontFamily: "B612, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  small:
    fontFamily: "B612, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.45
  caption:
    fontFamily: "B612, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  sm: "4px"
spacing:
  s1: "4px"
  s2: "8px"
  s3: "12px"
  s4: "16px"
  s5: "24px"
  s6: "32px"
  s7: "48px"
  tap: "44px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-text}"
    rounded: "{rounded.sm}"
    height: "{spacing.tap}"
  segmented-selected:
    backgroundColor: "{colors.seg-bg}"
    textColor: "{colors.seg-text}"
    rounded: "{rounded.sm}"
    height: "{spacing.tap}"
  flag-safety:
    backgroundColor: "{colors.caution-bg}"
    textColor: "{colors.caution}"
    rounded: "{rounded.sm}"
  flag-low:
    backgroundColor: "{colors.danger-bg}"
    textColor: "{colors.danger}"
    rounded: "{rounded.sm}"
  badge:
    textColor: "{colors.muted}"
    rounded: "{rounded.sm}"
---

# Design System: Training Hub

The product brief is PRODUCT.md; the user's own interface rules are "Interface design" in docs/spec.md and they
win over anything here. The tokens live in web/src/app.css; keep this file and that one in step.

## Overview

**North star: a personal flight instrument.** The app is read the way a pilot reads a display: the value that
matters, its trend, and anything unusual, at a glance and in poor conditions (one hand, just after a run, in
the sun or in the dark). It is quiet until something needs attention, and then it says so plainly.

Rework of 2026-10-15: the incumbent world (2026-10-12) is kept and sharpened: a 16 px body and a real 1.2
type scale, 44 px tap targets, curves larger than the text around them, and fewer words. Palette "warm
paper" (chosen by the user from three, 2026-10-15): warm neutrals, a soft ink instead of near-black, and a
teal accent, so the app is easier on the eye; warmth comes from the colours, space and type, never from
decoration.

Anti-references: SaaS dashboards of identical cards, hero-metric templates, gradients and glows, fitness-app
gamification (badges, streaks, confetti), and medical-device beige.

## Colors

Restrained: warm neutrals plus one accent, and colour only where it means something.

- **Accent (teal, #1f7a6c / dark #5fc2ad):** the main action, links, the selected place, and the glucose
  line in charts. Never decoration.
- **Current glucose (its own three colours, not the accent):** green in range (#195c2e / #79cd91), ochre above
  it up to 250 (#b08000 / #f1c45e), red below the low or above 250 (#cf4042 / #e36460). The word under the
  number ("in range", "above range", "above 250", "below range") is set in the same colour and bold, so the
  state never rests on colour alone (green against red is weak for red-green colour blindness). A stale
  reading is muted grey whatever its value.
- **Caution (amber, #8f540c / dark #e0a64a):** cautions only: a high start, ketone advice, above-range values.
- **Danger (red, #b23a2e / dark #ef7a6c):** a low, and destructive actions.
- **Neutrals:** bg, surface, border, text, muted and placeholder, in light and dark. Muted text stays at least
  4.5:1 against bg; placeholder too.
- **Selected value (.seg):** a tint of the accent (seg-bg) with dark teal text, not a block of ink.
- **Data colours** (charts only): the glucose line in the current number's three range colours (green in
  range, ochre above, red below the low or above 250; hard stops at the thresholds), one smooth line through
  live and 15-min history readings, broken only at real gaps (over 25 min); heart-rate zones as a slate-blue ordinal ramp
  (--zone-1..5, light to dark as effort rises; reversed in dark mode); comparison runs as a teal ordinal ramp (--step-1..5, the earlier cyan ramp turned to the
  accent's hue with its lightness steps kept) plus a warm grey --chart-unknown.

Light and dark are both designed. The theme follows the phone unless Settings pins one (data-theme on
<html>, applied before first paint).

## Typography

One family, B612 (Airbus' cockpit face), self-hosted. Tabular figures everywhere, so numbers line up.

Scale, ratio 1.2 from a 16 px body: caption 13, small 14, body 16, title 19 (section headings), heading 23
(page titles), display 36 (the main number of a page), hero 72 (current glucose). Headings are separated from
body text by size and weight, never by weight alone. Sentence case; no uppercase labels, no eyebrows above
headings. Units are always shown, set in muted text after the number.

## Layout

- Phone first: one column, 16 px gutters, content in reading order of importance. The main action sits at
  the bottom, in thumb reach.
- Desktop: the same order; charts take the full content width (up to about 1120 px), text stays within
  about 70 characters.
- Spacing on a 4 px grid (4, 8, 12, 16, 24, 32, 48). Tight inside a group, generous between groups, more space
  above a heading than below it.
- Navigation: four places (Now, Training, Analysis, Food) plus Settings; on the phone a bottom bar with Log
  in the middle, on desktop the same items in the top bar.
- Wide tables scroll inside their own frame and show that they do.

## Elevation & Depth

Flat. Separation comes from hairline rules and spacing, not shadows. The only layers are the Log sheet and
menus, which sit on the page background and cover it fully on the phone.

The Log sheet (rework step 5): the amounts first, Log right under them so it stays above the phone's
keyboard, then a row of quiet bordered buttons for what is pre-filled (insulin name, "Now", "Note"). The
choices (Raciones / Grams / Foods, Rapid / Basal) are segmented controls. The phone's back gesture closes
it (it has its own history entry), as Cancel does. On a keyboard, L opens it from anywhere outside a
text field. With a rapid dose logged now, the insulin field fills with the calculator's
suggestion (carbs plus correction, or the correction alone) and follows it until typed in; the line under it repeats the suggestion with "Why", and
"Use" when the field differs. Log's label names both amounts, so it is the confirmation.

## Shapes

Small radius (4 px) on buttons, segmented controls, badges, flag blocks and section panels; nothing else is
rounded. Inputs are a bottom rule, not a box. Each section of a page sits on a flat panel (.sec: the surface
colour, a 1 px border, no shadow), chosen 2026-10-15 because sections ran together as plain text; panels are
never nested and never a grid of identical cards, and rows inside one are separated by 1 px rules. On a wide
screen secondary panels pair up in two columns; charts take the full width. Status marks in the calendar are
told apart by shape (filled, half, dashed, round), not by colour.

## Components

- **Primary button:** accent fill, 44 px tall, bold label naming the action ("Log", "Save as a new version").
- **Text action (.link):** accent text; its hit area extends to 44 px without moving the text around it.
- **Tabs (.tabs):** navigation between views of one page: muted text, the current one in ink with an accent
  underline, 44 px tall.
- **Segmented control (.seg):** a choice between values (time window, insulin kind, week or month, theme):
  a bordered group, the selected value on a tint of the accent. Never used for navigation.
- **Safety flag (.flag-safety, .flag-low):** a short line on a tint of its colour, always first on the screen
  it belongs to. One line each; detail on demand.
- **Data-quality note (.flag-quality, .badge):** muted, beside the number it qualifies ("7.9 % coverage"),
  never in amber.
- **Glucose badge:** start value and change, bordered; amber border and text for a high start, red for a run
  that went low.
- **Charts (uPlot and SVG):** read their colours from the CSS variables and redraw on a theme change.

## Do's and Don'ts

- Do lead every screen with the numbers and curves it exists for, and say how complete they are.
- Do put safety points first and keep them plain; never soften them, never use the safety style for anything
  else.
- Do make anything tappable at least 44 px in both directions on the phone (app.css enforces 44 px height
  for every button, select and input on a touch screen; a mouse keeps the compact sizes).
- Do show a stale value as stale: an old reading never looks like a current one.
- Don't explain what the colour, the number or the heading already says (2026-10-15: no "in range" under
  the glucose number, no "Per the log.", no usage hints under charts). Keep a line only when it changes what
  the reader does or warns about something the screen cannot show.
- Don't use nested or decorative cards, gradients, glows, glass, emoji, coloured side borders, or decorative
  icons. A section panel is the only container.
- Don't use colour as the only carrier of meaning; say it in words or shape too.
- Don't suggest a dose anywhere outside the calculator, and don't style anything to look like advice.
