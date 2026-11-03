# Responsive Design — Reference

## Core Principle

Layouts should adapt to available space intrinsically. A well-built component doesn't need
to know which breakpoint it's in — it responds to the space it's given.

Media queries are a last resort, not a starting point.

## Technique Hierarchy

Work through this list top to bottom. Reach for the next technique only when the one above
can't solve the problem.

### 1. CSS Grid — `auto-fit` / `auto-fill` + `minmax()`

The primary tool for responsive multi-column layouts. Items wrap to new rows automatically
as the container shrinks, with no media queries.

```css
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr));
  gap: var(--space-md);
}
```

The `min(100%, 18rem)` inside `minmax()` prevents overflow on narrow viewports — items
never exceed their container.

**Use for:** card grids, gallery layouts, feature sections, any multi-item layout.

### 2. Flexbox + `flex-wrap`

Best for 1D layouts where items should flow and wrap based on available space.

```css
.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-sm);
}

.tag-list > * {
  flex: 1 1 auto;
}
```

**Use for:** tag lists, button groups, nav items, inline collections.

### 3. `clamp()` — Fluid Typography and Spacing

Scales a value smoothly between a minimum and maximum, typically using `vw` as the
flexible middle value.

```css
h1 {
  font-size: clamp(1.5rem, 1rem + 2vw, 3rem);
}

.section {
  padding-block: clamp(var(--space-md), 4vw, var(--space-2xl));
}
```

The formula `clamp(min, preferred, max)` ensures the value never goes below `min` or
above `max`, and scales fluidly in between.

**Use for:** headings, body text, section spacing, any value that should scale with viewport.

### 4. `min()` / `max()` — Adaptive Constraints

Create sizing rules that adapt without breakpoints.

```css
.container {
  width: min(90%, 72rem);
  margin-inline: auto;
}

.sidebar {
  width: max(20rem, 25%);
}
```

**Use for:** container widths, sidebar sizing, image dimensions, anything that needs a
ceiling or floor.

### 5. Fluid Units

Prefer relative and viewport-based units over fixed `px`:

| Unit   | Based on                    | Good for                        |
|--------|-----------------------------|---------------------------------|
| `rem`  | Root font size              | Spacing, font sizes, radii      |
| `em`   | Parent font size            | Component-local scaling         |
| `%`    | Parent dimension            | Widths, padding                 |
| `vw`   | Viewport width              | Fluid sizing (use inside clamp) |
| `vh`   | Viewport height             | Full-screen sections            |
| `dvh`  | Dynamic viewport height     | Mobile-safe full-height layouts  |
| `ch`   | Width of "0" character      | Prose line lengths               |
| `lh`   | Line height                 | Vertical rhythm                  |

Reserve `px` for borders, outlines, box shadows, and other values that should not scale.

### 6. `aspect-ratio`

Maintain proportions without the padding-bottom hack.

```css
.video-embed {
  aspect-ratio: 16 / 9;
  width: 100%;
}

.avatar {
  aspect-ratio: 1;
  border-radius: var(--radius-full);
}
```

**Use for:** video embeds, image thumbnails, avatars, any element needing fixed proportions.

### 7. Intrinsic Sizing Keywords

Let content dictate dimensions naturally.

```css
.badge {
  width: fit-content;
  padding-inline: var(--space-sm);
}

.table-cell {
  width: min-content;       /* shrink to narrowest */
  white-space: nowrap;
}

.hero-title {
  max-width: max-content;   /* never wraps unnecessarily */
}
```

**Use for:** badges, labels, table columns, any element that should size to its content.

### 8. Container Queries

When a component needs to respond to its parent container's size rather than the viewport.
This is essential for truly reusable components that work in sidebars, modals, and main
content areas alike.

```css
.card-wrapper {
  container-type: inline-size;
  container-name: card-container;
}

@container card-container (min-width: 30rem) {
  .card {
    --card-layout: row;
    --card-title-size: var(--font-lg);
  }
}

@container card-container (max-width: 29.999rem) {
  .card {
    --card-layout: column;
    --card-title-size: var(--font-base);
  }
}
```

Note that container query overrides still target component variables, not properties,
following the component variable API pattern.

**Use for:** components that appear in multiple layout contexts (sidebar vs main content),
reusable widgets, any component that can't assume a viewport width.

### 9. Media Queries — Last Resort

When none of the above can achieve the layout, use media queries. Even then, prefer
overriding component variables rather than redeclaring properties:

```css
@media (max-width: 479px) {
  .nav {
    --nav-direction: column;
    --nav-gap: var(--space-xs);
  }
}
```

Prefer `min-width` (mobile-first) for progressive enhancement or `max-width` when
adapting a desktop layout downward. Don't mix both approaches inconsistently within the
same codebase.
