# Component Variable API — Reference

## The Pattern

A component's visual surface is controlled entirely through custom properties that the
component itself defines. The chain is:

```
global token → component variable (with token fallback) → CSS property
```

Each link in the chain has a clear role:

- **Global tokens** set the design system defaults.
- **Component variables** let each component be independently tuned without leaking
  into other components.
- **CSS properties** are written once and never touched again.

## Building a Component

### Step 1 — Identify the visual properties

List every CSS property the component uses that could vary by context, theme, or viewport.
Common ones: background, color, padding, border-radius, font-size, gap, border.

### Step 2 — Create prefixed variables with token fallbacks

```css
@layer components {
  .card {
    background: var(--card-bg, var(--color-surface));
    color: var(--card-color, var(--color-text));
    padding: var(--card-padding, var(--space-md));
    border-radius: var(--card-radius, var(--radius-md));
    box-shadow: var(--card-shadow, var(--shadow-sm));
  }

  .card__title {
    font-size: var(--card-title-size, var(--font-lg));
    font-weight: var(--card-title-weight, 600);
  }
}
```

Every property flows through a `--card-*` variable. The fallback is a global token
(preferred) or a sensible raw value when no token fits.

### Step 3 — Override from the outside using variables

Consumers restyle the card by setting its variables — never by writing selectors that
reach into `.card` or `.card__title`.

```css
/* Parent context override */
.sidebar {
  --card-bg: var(--color-surface-raised);
  --card-padding: var(--space-sm);
  --card-shadow: none;
}

/* Modifier class override */
.card--featured {
  --card-bg: var(--color-accent);
  --card-color: #fff;
  --card-radius: var(--radius-lg);
}
```

This keeps the property declaration in one place. No matter how many contexts the card
appears in, `background: var(--card-bg, ...)` is written exactly once.

## Override Contexts

### Parent scope

A parent class sets component variables so all instances inside it adapt:

```css
.hero {
  --btn-bg: transparent;
  --btn-color: var(--color-text);
  --btn-radius: var(--radius-full);
}
```

Every `.btn` inside `.hero` gets the overridden look without additional selectors.

### Modifier classes

Modifier classes (BEM-style or otherwise) set variables on the component itself:

```css
.btn--ghost {
  --btn-bg: transparent;
  --btn-color: var(--color-accent);
}

.btn--sm {
  --btn-py: var(--space-xs);
  --btn-px: var(--space-sm);
  --btn-font-size: var(--font-sm);
}
```

### Media / container queries

Responsive changes target the component's variables, not its properties:

```css
@media (max-width: 479px) {
  .card {
    --card-padding: var(--space-sm);
    --card-title-size: var(--font-sm);
  }
}

@container (min-width: 40rem) {
  .card {
    --card-layout: row;
    --card-title-size: var(--font-lg);
  }
}
```

### Theme / dark mode

Components inherit theme changes for free because their fallbacks chain to global tokens.
If a component needs additional dark-mode tuning beyond what tokens provide, set its
variables inside the theme media query:

```css
@media (prefers-color-scheme: dark) {
  :root {
    /* Token-level changes — most components inherit automatically */
    --color-surface: var(--gray-900);
  }
  /* Component-specific dark-mode tweak */
  .card {
    --card-shadow: 0 2px 8px rgb(0 0 0 / 0.5);
  }
}
```

## Common Mistakes

**Writing the property twice:**
```css
/* BAD — background declared in two places */
.btn { background: var(--color-accent); }
.hero .btn { background: transparent; }

/* GOOD — background declared once, variable reassigned */
.btn { background: var(--btn-bg, var(--color-accent)); }
.hero { --btn-bg: transparent; }
```

**Skipping the component variable layer:**
```css
/* BAD — component reads token directly, can't be overridden per-instance */
.btn { background: var(--color-accent); }

/* GOOD — intermediate variable allows contextual override */
.btn { background: var(--btn-bg, var(--color-accent)); }
```

**Using raw values in fallbacks when a token exists:**
```css
/* BAD */
.card { padding: var(--card-padding, 1rem); }

/* GOOD — theme changes propagate */
.card { padding: var(--card-padding, var(--space-md)); }
```

**Targeting internals from the outside:**
```css
/* BAD — parent reaches into component markup */
.sidebar .card h3 { font-size: 0.875rem; }

/* GOOD — parent sets the component's variable */
.sidebar { --card-title-size: var(--font-sm); }
```
