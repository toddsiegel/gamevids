# Layers & Design Tokens — Reference

## Layer Stack Deep Dive

The `@layer` declaration at the top of the stylesheet establishes a fixed priority order.
Declarations in higher layers override lower layers regardless of selector specificity.
This eliminates specificity wars entirely.

### Declaring layers

Always declare the full stack in a single statement so the order is explicit:

```css
@layer reset, base, tokens, layout, components, utilities, overrides;
```

Then populate each layer with `@layer <name> { ... }` blocks, or assign imported sheets:

```css
@import url("normalize.css") layer(reset);
@import url("tailwind-utilities.css") layer(utilities);
```

### What goes where

**reset** — Normalize browser defaults. Typically box-sizing, margin removal, and
consistent form control appearance. Keep it minimal.

```css
@layer reset {
  *, *::before, *::after { box-sizing: border-box; }
  body { margin: 0; }
}
```

**base** — Global element styles that apply to bare HTML elements (no classes). Typography
defaults, link colors, list styles. These form the "unstyled but readable" baseline.

```css
@layer base {
  body {
    font-family: system-ui, sans-serif;
    line-height: 1.6;
    color: var(--color-text);
    background: var(--color-surface);
  }
  a { color: var(--color-accent); }
}
```

**tokens** — Only custom property definitions. No selectors other than `:root` (and
media/container query variants of `:root`). This is the theme layer.

**layout** — Page-level structural patterns: grid systems, container width constraints,
sticky headers, sidebar + main. These define where content regions sit, not how individual
components look.

```css
@layer layout {
  .page-grid {
    display: grid;
    grid-template-columns: var(--sidebar-width, 16rem) 1fr;
    min-height: 100dvh;
  }
  .container {
    width: min(90%, 72rem);
    margin-inline: auto;
  }
}
```

**components** — Self-contained UI patterns (buttons, cards, modals, navbars). Each one
exposes its variable API and writes every property exactly once.

**utilities** — Single-purpose helper classes. Tailwind's generated classes belong here.
Hand-written utilities (`.sr-only`, `.flex`, `.mt-4`) go here too.

```css
@layer utilities {
  .sr-only {
    position: absolute; width: 1px; height: 1px;
    overflow: hidden; clip: rect(0 0 0 0);
  }
  .flex { display: flex; }
}
```

**overrides** — Last-resort fixes. Third-party widget patches, CMS-injected style
corrections, or edge-case workarounds. If a rule ends up here, leave a comment explaining
why — overrides should be rare and temporary.

### Layer interaction with `!important`

Inside `@layer`, `!important` inverts the priority order — an `!important` in a lower layer
beats one in a higher layer. This is by design in the spec. Avoid `!important` entirely;
the layer stack makes it unnecessary.

---

## Design Token System

### Naming structure

Use two tiers of tokens:

1. **Primitive tokens** — raw values named by what they are.
   `--blue-500`, `--gray-100`, `--font-sans`

2. **Semantic tokens** — role-based names that reference primitives.
   `--color-accent: var(--blue-500)`, `--color-surface: var(--gray-100)`

Components always consume semantic tokens. Theme switches reassign semantic tokens to
different primitives — components never need to change.

### Recommended token categories

| Category   | Prefix        | Examples                                    |
|----------- |-------------- |---------------------------------------------|
| Color      | `--color-`    | `--color-accent`, `--color-text`, `--color-surface` |
| Spacing    | `--space-`    | `--space-xs` (0.25rem) through `--space-2xl` (4rem) |
| Typography | `--font-`     | `--font-sm`, `--font-base`, `--font-lg`     |
| Radii      | `--radius-`   | `--radius-sm`, `--radius-md`, `--radius-full` |
| Shadows    | `--shadow-`   | `--shadow-sm`, `--shadow-md`, `--shadow-lg`  |
| Duration   | `--duration-` | `--duration-fast` (150ms), `--duration-normal` (300ms) |

### Theming via token reassignment

A theme is nothing more than a set of token reassignments on `:root`:

```css
/* Default (light) theme is defined in @layer tokens. */

/* Dark theme — reassign semantic tokens only */
@media (prefers-color-scheme: dark) {
  :root {
    --color-accent: var(--blue-400);
    --color-text: var(--gray-100);
    --color-surface: var(--gray-900);
    --shadow-md: 0 4px 12px rgb(0 0 0 / 0.4);
  }
}

/* Class-based theme toggle (same idea) */
:root.theme-dark {
  --color-accent: var(--blue-400);
  --color-text: var(--gray-100);
  --color-surface: var(--gray-900);
}
```

No component CSS changes. Every component that references `--color-accent` or
`--color-surface` inherits the new values automatically.
