# Research Report: Prebuilt Chat-UI Packages and Markdown/Code Rendering Libraries for Web Frontends

## Introduction

This report evaluates prebuilt chat interface components, UI kits, markdown parsers, syntax highlighters, and HTML sanitizers available on npm. Particular emphasis is placed on compatibility with the existing `/ui` architecture, which currently ships plain TypeScript compiled output (`tsc`) loaded directly via native ES modules (`<script type="module">`) without a client-side bundler (such as Vite, Webpack, or Rollup).

---

## 1. Ready-Made Chat Interface Components & UI Kits on npm

Most modern chat UI component libraries on npm (such as `@ai-sdk/react` chat components, `@chatui/core`, or React-based UI kits like Chatwoot widgets, Stream Chat React, or Ably Chat UI components) are built specifically for React, Vue, or Svelte, and rely heavily on JSX, component lifecycles, and bundlers to resolve node module imports.

For a vanilla TypeScript application using native ESM without a bundler, importing heavy React-based UI kits directly in the browser via `<script type="module">` is not feasible without a build step or import maps pointing to ESM CDNs (like esm.sh or unpkg). However, building lightweight custom DOM-based components or using framework-agnostic web components/CSS UI kits is vastly superior for the current unbundled setup.

### Evaluated Options:
1. **Custom Vanilla JS/TS DOM Components (Recommended for `/ui`)**
   - **Framework:** Vanilla JavaScript / TypeScript (Framework-agnostic)
   - **Bundler:** None required (runs natively with ES modules via `<script type="module">`)
   - **Description:** Crafting modular DOM renderers for message lists, chat bubbles, streaming indicator (`div` spinners / live output pre-blocks), and composer textareas.
   - **Licence:** MIT / Project Proprietary
   - **Weight:** Negligible (~2-5 KB custom wrapper code)
   - **Maintenance:** Actively maintained as part of the project repository.

2. **Stream Chat / Ably Chat UI Components**
   - **Framework:** React / Vue (Framework-specific)
   - **Bundler:** Requires a bundler (Vite/Webpack) or ESM CDN import maps with full dependency graphs.
   - **Licence:** MIT / Commercial
   - **Weight:** Heavy (100KB+ gzipped with dependencies)
   - **Maintenance:** Actively maintained by platform vendors.

---

## 2. Markdown + Code Rendering, Syntax Highlighting, and Sanitization Libraries

For rendering assistant markdown messages with code blocks and syntax highlighting while preventing XSS vulnerabilities, a combination of three robust, standalone libraries is standard across the industry:

### A. Markdown Parser: `marked`
- **What it does:** Fast, lightweight markdown parser that compiles markdown strings into HTML without external dependencies.
- **Framework:** Framework-agnostic (Vanilla JS/TS).
- **Bundler:** Works seamlessly as an ESM import (e.g., from `https://esm.sh/marked@12.0.0` or bundled via `tsc` when installed locally).
- **Licence:** MIT
- **Weight:** ~15 KB minified / ~5 KB gzipped.
- **Last Release / Maintenance:** Actively maintained (v12+ released recently).

### B. Syntax Highlighting: `highlight.js`
- **What it does:** Syntax highlighting for code blocks in 190+ languages with automatic language detection and CSS themes.
- **Framework:** Framework-agnostic (Vanilla JS/TS).
- **Bundler:** Works with ES modules; language modules or core can be imported directly.
- **Licence:** BSD-3-Clause
- **Weight:** ~20-50 KB depending on language bundle size.
- **Last Release / Maintenance:** Highly active and maintained.

### C. HTML Sanitizer: `DOMPurify`
- **What it does:** Ultra-fast, DOM-only XSS sanitizer for HTML, preventing malicious script injection from parsed markdown.
- **Framework:** Framework-agnostic (Vanilla JS/TS).
- **Bundler:** Native ESM support; works directly in browser and Node.js.
- **Licence:** MPL-2.0 / Apache-2.0 (Dual licensed)
- **Weight:** ~20 KB minified / ~7 KB gzipped.
- **Last Release / Maintenance:** Extremely active (v3.4.x released recently).

---

## 3. Framework & Bundler Compatibility Analysis

| Library / Component | Framework Required | Bundler Required? | Notes for `/ui` (`<script type="module">`) |
| :--- | :--- | :--- | :--- |
| **Custom Vanilla DOM UI** | None (Vanilla TS) | No | Perfectly matches current `/ui` setup. |
| **`marked`** | None (Vanilla JS/TS) | No | Can be imported via CDN (`esm.sh`) or local npm package. |
| **`highlight.js`** | None (Vanilla JS/TS) | No | Requires CSS stylesheet inclusion for syntax themes. |
| **`DOMPurify`** | None (Vanilla JS/TS) | No | Essential security layer before setting `innerHTML`. |
| **React UI Kits (e.g. Shadcn/Stream)** | React | Yes (Vite/Webpack) | Incompatible with unbundled `<script type="module">` without a build step. |

---

## 4. Summary Table of Recommended Libraries

| Library | Version / Release | Licence | Minified / Gzipped Weight | Maintenance Status | Framework / Bundler Needed |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`marked`** | v12.x | MIT | ~15 KB / ~5 KB | Active | None / None |
| **`highlight.js`** | v11.x | BSD-3-Clause | ~40 KB / ~12 KB | Active | None / None |
| **`DOMPurify`** | v3.4.x | MPL-2.0 / Apache-2.0 | ~20 KB / ~7 KB | Active | None / None |
| **Custom DOM UI** | v1.0 | MIT | < 2 KB | Active | None / None |

---

## 5. Conclusion & Recommendation

For the unbundled `/ui` architecture (`tsc` output loaded via `<script type="module">`), introducing heavy React-based chat UI kits or component libraries would violate the zero-bundler constraint or require a major architectural rewrite to add Vite/Webpack.

**Recommended Approach:**
1. Keep the chat interface layout constructed via vanilla TypeScript DOM manipulation in `/ui/src/client/app.ts`.
2. Integrate **`marked`** for markdown parsing, **`highlight.js`** for code block syntax highlighting, and **`DOMPurify`** to sanitize the generated HTML before rendering into message bubbles.
3. Include the corresponding CSS theme for `highlight.js` in `style.css` to ensure code blocks render with professional syntax colors.

### Sources
[1] marked npm package: https://www.npmjs.com/package/marked
[2] highlight.js npm package: https://www.npmjs.com/package/highlight.js
[3] DOMPurify npm package: https://www.npmjs.com/package/dompurify
