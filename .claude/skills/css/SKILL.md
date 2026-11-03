---
name: Writing and organizing CSS
description: Guidelines for writing, organizing amd maintaining CSS.
---

## Responsive Design
All pages must be responsive to work on a variety of screen sizes. Prefer intrinsic, media-query-free techniques. Reach for media queries only when no reasonable alternative exists.

Use these techniques:

### Prefer CSS Grid for multi-column layouts
`auto-fit` / `auto-fill` + `minmax()`
```css
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr));
  gap: var(--space-md);
}
```

2. **Flexbox + `flex-wrap`** — 1D layouts where items flow and wrap with available space.

3. **`clamp()`** — fluid typography and spacing that scales smoothly.
   ```css
   h1 { font-size: clamp(1.5rem, 1rem + 2vw, 3rem); }
   ```

4. **`min()` / `max()`** — sizing constraints that adapt without breakpoints.
   ```css
   .container { width: min(90%, 72rem); margin-inline: auto; }
   ```

5. **Fluid units** (`%`, `vw`, `vh`, `ch`, `rem`) — prefer these over fixed `px` values.

6. **`aspect-ratio`** — maintain proportions on media embeds and visual elements.

7. **Intrinsic sizing** (`fit-content`, `min-content`, `max-content`) — let content dictate size.

8. **Container queries** — when a component must respond to its parent's size, not the viewport.
   ```css
   .card-wrapper { container-type: inline-size; }
   @container (min-width: 30rem) {
     .card { --card-layout: row; }
   }
   ```

Media queries come last, for situations none of the above can handle.

EDIT EVERYTHIGNG BELOW HERE
=================================================================================
# Modern CSS: Writing & Organizing with Baseline Features

This skill defines a decision hierarchy for writing CSS that is maintainable, themeable, and
responsive — using only features that have landed in the CSS baseline (no preprocessors required).

The core principle: **every visual property is written exactly once**. Theming, responsive
adaptation, and contextual restyling all work by reassigning variables — never by redeclaring
CSS properties in multiple places.

Read `references/layers-and-tokens.md` for detailed guidance on the layer stack and token system.
Read `references/component-api.md` for the component variable pattern and override rules.
Read `references/responsive.md` for the intrinsic-first responsive technique hierarchy.

---

## 1. Layer Architecture

Declare a `@layer` stack at the top of the main stylesheet. Layer order determines cascade
priority — a declaration in a higher layer always wins regardless of selector specificity.

```css
@layer reset, base, tokens, layout, components, utilities, overrides;
```

| Layer        | Purpose                                              |
|------------- |------------------------------------------------------|
| reset        | Browser normalization (e.g. box-sizing, margin: 0)   |
| base         | Global element styles (body, headings, links, lists)  |
| tokens       | Design tokens as custom properties on :root           |
| layout       | Page structure — grids, containers, regions           |
| components   | Reusable UI pieces (buttons, cards, modals, navs)     |
| utilities    | Single-purpose helpers (.flex, .sr-only); Tailwind goes here |
| overrides    | Third-party patches and last-resort fixes             |

Place every rule inside its correct layer. Never rely on source order or specificity tricks
to win the cascade — let the layer stack do the work.

## 2. Design Token System

Define global tokens on `:root` inside `@layer tokens`. Tokens are the single source of truth
for palette, spacing, radii, typography, and shadows.

```css
@layer tokens {
  :root {
    /* Primitive palette */
    --blue-500: #3b82f6;
    --gray-900: #0f172a;

    /* Semantic tokens — reference primitives */
    --color-accent: var(--blue-500);
    --color-text: var(--gray-900);
    --color-surface: #fff;

    /* Spacing scale */
    --space-xs: 0.25rem;
    --space-sm: 0.5rem;
    --space-md: 1rem;
    --space-lg: 2rem;

    /* Typography */
    --font-sm: 0.875rem;
    --font-base: 1rem;
    --font-lg: 1.25rem;

    /* Radii */
    --radius-sm: 0.25rem;
    --radius-md: 0.5rem;
    --radius-full: 9999px;
  }
}
```

**Naming convention:** use `--category-variant` (e.g. `--color-accent`, `--space-md`,
`--radius-sm`). Distinguish primitive tokens (raw values like `--blue-500`) from semantic
tokens (role-based like `--color-accent`) so that theme switches only reassign semantic tokens.

**Dark mode** works by reassigning tokens — never by rewriting component styles:

```css
@media (prefers-color-scheme: dark) {
  :root {
    --color-accent: #60a5fa;
    --color-text: #e2e8f0;
    --color-surface: #0f172a;
  }
}
```

Components inherit the new values automatically through their variable fallback chains.

## 3. Component Variable API

Each component exposes a set of `--component-*` custom properties. All CSS declarations inside
the component reference these variables, which fall back to global tokens. Consumers restyle
the component by setting its variables from a parent context — never by targeting its internal
selectors.

```css
@layer components {
  .btn {
    background: var(--btn-bg, var(--color-accent));
    color: var(--btn-color, #fff);
    padding: var(--btn-py, var(--space-sm)) var(--btn-px, var(--space-md));
    border-radius: var(--btn-radius, var(--radius-md));
    font-size: var(--btn-font-size, var(--font-base));
  }
}
```

**Contextual override — parent sets the variables, never the properties:**

```css
.hero {
  --btn-bg: transparent;
  --btn-color: var(--color-text);
  --btn-radius: var(--radius-full);
}
```

**Responsive override — change component variables, not declarations:**

```css
@media (max-width: 479px) {
  .card {
    --card-padding: var(--space-sm);
    --card-title-size: var(--font-sm);
  }
}
```

Rules:
- Prefix every component variable with the component name (`--card-*`, `--btn-*`, `--nav-*`).
- Use token references in fallbacks rather than raw values, so theme changes propagate.
- Write every visual property exactly once in the component block. All variation happens
  by reassigning variables.

## 5. Anti-Patterns to Avoid

These patterns undermine the architecture. Flag and refactor them:

- **Targeting component internals from outside.** Don't write `.parent .btn span { ... }`.
  Set the component's custom property variables instead.
- **Raw values where tokens exist.** Don't write `padding: 16px` when `var(--space-md)`
  is available. Raw values break theme propagation.
- **Media queries before intrinsic techniques.** If `auto-fit + minmax()` or `clamp()`
  solves the problem, don't add a breakpoint.
- **Redeclaring CSS properties for variation.** If `background` appears in both `.btn`
  and `.hero .btn`, the property is written twice. Reassign `--btn-bg` instead.
- **Specificity wars.** Adding IDs, `!important`, or deeply nested selectors to win the
  cascade. Use the layer stack instead.
- **Styling with Tailwind utilities what should be a component.** If a pattern repeats
  three or more times with the same set of utilities, extract it into a component in
  `@layer components` with its own variable API.
