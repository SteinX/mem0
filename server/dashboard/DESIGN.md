# Mem0 Dashboard design system

## 1. Atmosphere and identity

The existing Dashboard uses a light neutral command surface, a persistent navigation rail, bordered cards, and compact controls. Upgrade fixes preserve that visual identity. The implementation sources are `src/styles/globals.css`, `tailwind.config.ts`, and `src/components/ui`.

## 2. Color

Use the existing semantic Tailwind tokens: `surface-default-primary`, `surface-default-secondary`, `onSurface-default-primary`, `onSurface-default-secondary`, `memBorder-primary`, `primary`, `muted`, `destructive`, and their declared hover/status variants. Their light and dark values remain in the CSS variables in `globals.css`. No new palette is needed for responsive fixes.

## 3. Typography

Retain the existing Inter/Fustat body and display families and DM Mono identifiers. The current type scale uses `text-xs` for metadata, `text-sm` for controls and secondary copy, `text-base` for body content, and `text-lg`/`text-xl` for headings. Long identifiers and emails may wrap; truncation needs an accessible full-value alternative. Preserve legible CJK fallback glyphs and natural line breaks.

## 4. Spacing and layout

Use the existing four-pixel Tailwind spacing scale, especially `gap-2`, `gap-4`, `p-4`, `p-6`, and `space-y-4`. The navigation rail widths remain the shared constants in `clientLayout.tsx`; the content shell subtracts the active rail width from the viewport. Standard Tailwind breakpoints apply. At mobile widths, cards and forms must shrink to the shell, controls may stack, and intrinsic content must not force a larger hidden canvas. Explicit data-table scroll regions own horizontal overflow. These are layout mechanics, not new spacing or color tokens.

## 5. Reused primitives and states

`Button`, `Input`, `Label`, `Card`, `Badge`, `RadioGroup`, `Table`, and `ScrollArea` are existing shared DOM components. Preserve their default, hover, focus, disabled and loading states and keyboard semantics. Forms keep visible labels and existing validation/error feedback. Cards use the existing border and radius tokens. Page content and tables remain live components; raster images never substitute for controls or text. The existing export fixture harness exercises create, successful download, failed download, and loading/empty route states across desktop, tablet and mobile.

## 6. Motion and interaction

Retain existing navigation/overlay transitions and reduced-motion behavior. Responsive corrections add no decorative animation. Async actions retain their disabled/loading states, completion feedback and error messages.

## 7. Accessibility and responsive behavior

Maintain real links, buttons, labelled inputs, focus indicators and accessible error text. Content must remain reachable at narrow widths through wrapping, stacking or an explicit scroll region. Do not hide controls or data to repair overflow. Check all ten Dashboard routes at desktop, tablet and mobile widths with the actual browser and current source.

## 8. Change discipline and verification

Reuse existing tokens and primitives; introduce no new brand styling or runtime dependencies for layout fixes. Verify source typechecks and the production build, then inspect fresh complete route captures and actual export/error interactions. Source and overlay twins must match where the Sidecar overlay owns a file. Record any pre-existing issue separately from release changes.
