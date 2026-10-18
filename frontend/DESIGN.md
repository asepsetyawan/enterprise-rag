# Knowledge workspace UI

## Design plan

- Paper #ffffff, canvas #f5f7fc, ink #202b43, muted #63718a, cobalt #3459d4, amber #b66e12.
- IBM Plex Sans, locally bundled. Large conversational headings, restrained interface text, generous reading line height.
- A light navigation spine anchors an asymmetric workspace. The question composer is the focal point; the source index is a continuous column, not another floating card. Evidence belongs directly below an answer.

Desktop:

```
| CNF       | workspace breadcrumb                     user context |
| workspace |                                                      |
| navigation| Knowledge heading                  | Source index    |
|           | Large question composer            | Document        |
|           | Suggested question rows            | Document        |
|           | Answer + numbered citations        | Access note     |
| identity  |                                                      |
```

Mobile:

```
| brand / workspace |
| scrollable nav    |
| heading           |
| composer          |
| source disclosure |
| answer / guidance |
```

- Knowledge-first hierarchy: document names and citations stay readable; identifiers are secondary.
- Counts derive from live data. Suggestions only fill the question; operational actions retain their existing permissions.
- Keyboard focus, native file controls, responsive tables, reduced motion, and local fonts support an internal deployment.

## Self-review before implementation

A dark sidebar with white rounded cards would repeat the previous dashboard. Use a light navigation spine, a single strong composer, unboxed suggestion rows and a continuous source index instead. Avoid uppercase tracked labels and decorative statistics. Keep the login visual tied to a document-to-answer reading flow. Cobalt is reserved for active navigation and actions; amber communicates restricted access rather than decoration.

## Verification scope

Frontend compilation and focused browser inspection of chat, document library, operations and permission dialog. No backend, inference, recovery or full evaluation rerun for this visual change.

## Visual review

The first browser pass showed that secondary labels and document actions were too small and faint. Increased their type sizes and contrast, reduced permission dialog spacing, and retained a visible logout control on mobile. Removed the source-column gradient; alignment and the divider provide the grouping. Verified the native permission dialog without saving an access change.
