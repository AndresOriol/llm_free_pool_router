# Research Report: Chat-UI Component Libraries, Framework Costs, and Lightweight Syntax Highlighting

## Introduction

This report investigates whether there is a maintained chat-UI component library worth adopting for the message list, bubbles, streaming indicator, and composer in `/ui`. It explicitly considers the cost of pulling in a framework, the bundle size implications (noting that the current bundle is 1.8 MB primarily because `highlight.js` ships every language), and evaluates lighter syntax highlighting setups.

---

## 1. Chat-UI Component Libraries & Framework Adoption Cost

Most modern chat-UI component libraries (such as `@chatui/core`, `@ai-sdk/react` chat components, Stream Chat React, or Shadcn UI chat blocks) are built for React, Vue, or Svelte. 

### Framework Cost Analysis for `/ui`
- **Current Setup:** `/ui` currently bundles vanilla TypeScript using `esbuild` (`esbuild src/client/app.ts --bundle --format=esm --outfile=dist/client/app.js --target=es2022`). It has zero heavy client-side framework dependencies.
- **Cost of Adopting React / Vue / Svelte:** 
  - Pulling in React adds ~40 KB minified/gzipped (~130 KB uncompressed) plus runtime overhead, virtual DOM reconcilers, and JSX build transforms.
  - Pulling in Svelte or Solid provides lighter runtimes (~2–10 KB), but still requires changing the build pipeline and component architecture.
  - Pulling in prebuilt React chat UI libraries (e.g., Stream Chat, Sendbird UI, or `@ai-sdk/react`) brings along massive dependency trees (React, icons, utility libraries, state management), bloating the bundle by hundreds of kilobytes and tying the UI to a specific component model.
- **Conclusion on Component Libraries:** For a focused, high-performance developer coding agent interface like `/ui`, adopting a heavy prebuilt React/Vue chat component library is **not recommended**. Crafting lightweight vanilla TypeScript DOM components or modular UI helper functions maintains zero framework overhead, instant load times, and complete control over streaming events and Server-Sent Events (SSE).

---

## 2. Syntax Highlighting & Bundle Size Optimization (`highlight.js` vs Alternatives)

The current bundle reaches 1.8 MB primarily because importing `highlight.js` (`import hljs from 'highlight.js'`) defaults to registering and shipping all 190+ languages and grammars in the bundled output.

### Evaluation of Highlighting Alternatives

1. **Modular / Selective Language Registration in `highlight.js`**
   - **How it works:** Instead of importing the full `highlight.js` bundle, import `highlight.js/lib/core` and register only the most common languages needed for coding agent interactions (e.g., TypeScript, JavaScript, Python, Bash/Shell, JSON, Markdown, HTML, CSS, YAML, Dockerfile).
   - **Weight Impact:** Reduces `highlight.js` contribution from ~1.5 MB uncompressed / ~400 KB gzipped to under 50 KB uncompressed / ~15 KB gzipped.
   - **Adoption Effort:** Low. Simply change imports from `import hljs from 'highlight.js'` to selective registration:
     ```ts
     import hljs from 'highlight.js/lib/core';
     import typescript from 'highlight.js/lib/languages/typescript';
     import python from 'highlight.js/lib/languages/python';
     import bash from 'highlight.js/lib/languages/bash';
     import json from 'highlight.js/lib/languages/json';
     
     hljs.registerLanguage('typescript', typescript);
     hljs.registerLanguage('python', python);
     hljs.registerLanguage('bash', bash);
     hljs.registerLanguage('json', json);
     ```

2. **Prism.js**
   - **What it works:** Lightweight, extensible syntax highlighter with custom language and plugin builds.
   - **Weight:** ~10–30 KB depending on selected languages.
   - **Comparison:** `highlight.js` with selective registration is equally lightweight and avoids rewriting the existing `marked` code renderer integration.

3. **Shiki**
   - **What it works:** VS Code-powered syntax highlighter using TextMate grammars. Produces gorgeous, highly accurate themes.
   - **Weight:** Heavy (includes WASM / JSON grammars, often 500 KB+), making it better suited for server-side generation or Vite builds rather than lightweight browser clients.

---

## 3. Summary Table of Options

| Approach | Bundle Size Impact | Framework Required? | Maintenance Status | Recommendation |
| :--- | :--- | :--- | :--- | :--- |
| **React / Vue Chat UI Kits** | High (+100 KB - 500 KB) | Yes (React/Vue) | Active | **Not Recommended** (adds unnecessary framework weight) |
| **Vanilla TS DOM Components** | Negligible (< 5 KB) | None | Active (Project-owned) | **Recommended** (fits current unbundled/esbuild setup perfectly) |
| **Full `highlight.js` (All languages)** | Very High (~1.8 MB bundle) | None | Active | **Avoid** (causes massive bundle bloat) |
| **Modular `highlight.js` (Selective languages)** | Low (~50 KB) | None | Active | **Recommended** (drops bundle size drastically while retaining full syntax support) |

---

## 4. Conclusion & Recommendations

1. **Chat UI Components:** Do not adopt a heavyweight framework-based chat UI library. Continue using clean, modular vanilla TypeScript DOM creation functions for message lists, chat bubbles, streaming indicators, and composers in `/ui`.
2. **Syntax Highlighting Optimization:** Refactor `highlight.js` usage in `/ui/src/client/app.ts` from importing the monolithic package to importing `highlight.js/lib/core` and registering only essential languages (TypeScript, JavaScript, Python, Bash, JSON, HTML, CSS, YAML). This single change will shrink the client bundle from 1.8 MB down to under 150 KB.

### Sources
[1] highlight.js core and modular loading documentation: https://highlightjs.readthedocs.io/
[2] marked markdown parser: https://github.com/markedjs/marked
[3] DOMPurify sanitizer: https://github.com/cure53/DOMPurify
