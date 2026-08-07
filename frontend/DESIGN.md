# OpenManus design system

The product is a workbench for watching and steering an agent that operates a
computer. People keep it open for hours and scan it repeatedly, so the system is
dense, quiet and scannable — not a landing page. Colour is the main carrier of
meaning; typography and spacing stay disciplined so the colour can do that job.

## The one rule

**Colour means something.** Every hue in the interface either identifies a *kind
of agent work* or belongs to the brand chrome. Nothing is tinted for decoration.
If you find yourself reaching for a Tailwind palette colour directly
(`text-purple-500`), that is the signal you are inventing a meaning the system
does not have — add it here instead.

## Activity hues

Six kinds of work, six hues. Defined in `src/styles/globals.css`, consumed only
through `src/libs/activity.ts`.

| Kind       | Hue     | Covers                                        |
| ---------- | ------- | --------------------------------------------- |
| `think`    | violet  | reasoning, planning, tool selection           |
| `tool`     | amber   | generic tool execution                        |
| `browser`  | cyan    | page visits, screenshots, web search          |
| `file`     | emerald | workspace writes and diffs                    |
| `terminal` | slate   | shell and python output                       |
| `error`    | rose    | failures, terminations                        |

Each hue ships three tokens: the base (`--activity-file`) for icons and text, a
tinted `-surface` for chips and thumbnails, and a `-border` hairline. Use the
trio together — base text on its own surface is contrast-checked, base text on
an arbitrary background is not.

`activityKindFor(eventType, toolName)` is the only place event types are mapped
to kinds. It refines by tool name: `bash` and `python_execute` read as terminal
work, `browser_use` and `web_search` as browser work, regardless of the fact
that the lifecycle calls all of them tool events.

## Brand

`--brand` (indigo) with `--brand-accent` (cyan) is **structural chrome only**:
active tab underline, focus ring, progress fill, the live pulse, primary
buttons. It never labels content — that is what the activity hues are for.

Deliberately no large gradient fills, no glow, no glass. A gradient appears in
exactly one place, the 2px active-tab underline, where it reads as identity
rather than decoration.

## Neutrals

Neutrals carry a small amount of chroma on a cool hue (265) instead of being
pure gray. Before this change every token was `oklch(L 0 0)` — literal
grayscale, the shadcn default. Tinted neutrals read as a chosen surface and let
the activity hues sit on top without the page splitting into two unrelated
systems.

## Contrast

Every foreground/background pairing is verified to WCAG 2.2 AA, in both themes,
including each activity hue on its own tinted surface. Re-run after touching any
colour token:

```
python3 scripts/check-contrast.py frontend/src/styles/globals.css
```

Minimum margins are intentional: no pairing sits below 5.0:1 for text, so small
type and antialiasing differences do not push anything under the line.

## Motion

Motion says "this is live" or orients you when a panel swaps. Nothing loops for
decoration.

- `.live-dot` — the single ambient animation in the product.
- `.moment-in` — 180ms entrance when the timeline selection changes.
- `.writing-caret` — trailing caret on text the agent is still producing.

All of it collapses under `prefers-reduced-motion: reduce`.

## Anti-patterns for this repo

- Cards nested inside cards. A panel renders its own container; the shell must
  not wrap it in a second one.
- Rows of unlabelled icon buttons. If there are more than about four
  destinations, they are tabs and they need words.
- Reaching for a raw Tailwind colour instead of an activity token.
- Animating anything on a loop that is not communicating live state.
