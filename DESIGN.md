# Fieldwork design system

A task-oriented personal research dashboard. Product truth lives in PRODUCT.md; the user confirmed trends-first navigation. Layout uses standard sidebar navigation, scoped filters, semantic tables and evidence detail. The visual system draws on the warm paper and restrained greens of botanical field references, without imitating a paper artifact. The signature is a daily observation strip whose explicit gaps expose collection health. Green selection is reserved for navigation; amber signals limited quality and red means an error.

## Tokens

| Token | Value | Use |
| --- | --- | --- |
| ink | #183d36 | Main text |
| muted | #536b63 | Secondary text |
| paper | #f4f3ec | Workspace |
| surface | #fffef9 | Inputs and evidence |
| line | #d2dacf | Table and section dividers |
| rail | #193e36 | Navigation and primary action |
| accent | #b7d960 | Active navigation |
| warning | #765116 | Limited quality text |
| error | #9f352e | Action error text |

Body uses Avenir Next, Avenir, Segoe UI and sans-serif; headings 28–40px, section headings 21px, body 15px and table text 13px. Data uses tabular numerals. Section spacing is 28–42px, related controls 8–20px. Under 760px the rail becomes a horizontally scrollable navigation region and forms become single-column. Focus rings, selection colors, native labelled controls and a reduced-motion preference are implemented. Job text is rendered as escaped plain text.

Browser screenshot verification is outstanding due to unavailable/timed-out browser automation. Source and build checks cannot establish visual QA. The direction seed ran in degraded mode without remote challenger references; no user-approved visual identity is claimed.
