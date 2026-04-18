# UI Contrast Fixes + RAG Relevance Score Optimization

## Context
The chat UI has severe contrast issues where light-theme colors are used on a dark `#0F172A` background. Additionally, the min_relevance_score default of 0.7 is too aggressive for a growing document corpus.

## Files to Change

### 1. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/thread/markdown-styles.css`
**Replace entire file content with:**
```css
/* Base markdown styles — dark theme */
.markdown-content code:not(pre code) {
  background-color: rgba(255, 255, 255, 0.06);
  padding: 0.2em 0.4em;
  border-radius: 3px;
  font-size: 0.9em;
}

.markdown-content a {
  color: #0EA5E9;
  text-decoration: none;
}

.markdown-content a:hover {
  text-decoration: underline;
}

.markdown-content blockquote {
  border-left: 4px solid #334155;
  padding-left: 1rem;
  color: #94A3B8;
}

.markdown-content pre {
  overflow-x: auto;
}

.markdown-content table {
  border-collapse: collapse;
  width: 100%;
}

.markdown-content th,
.markdown-content td {
  border: 1px solid #334155;
  padding: 8px;
}

.markdown-content th {
  background-color: #1E293B;
}

.markdown-content tr:nth-child(even) {
  background-color: #0F172A;
}
```

### 2. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/thread/chat-theme.css`
**Find and replace:**
- Line with `color: #0f172a` in textarea/input/select → change to `color: hsl(var(--text-main))`
- Line with `placeholder: #475569` → change to `placeholder: hsl(var(--muted))`

### 3. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/ui/badge.tsx`
**Replace:**
- `bg-gray-100 text-gray-900` → `bg-border text-text-main` (in secondary variant)
- `text-gray-900 border-gray-300` → `text-text-main border-border` (in outline variant)

### 4. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/ui/slider.tsx`
**Replace:**
- `bg-gray-200` → `bg-border` (track background)

### 5. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/thread/source-display.tsx`
**Systematic replacements throughout the file:**

| Replace | With |
|---------|------|
| `border-blue-200 bg-blue-50/50` | `border-border bg-surface` |
| `text-blue-900` | `text-text-main` |
| `text-blue-600` | `text-primary` |
| `text-purple-700` | `text-primary` |
| `bg-purple-50` | `bg-background` |
| `text-gray-800` | `text-text-main` |
| `text-gray-700` | `text-text-main` |
| `text-gray-500` | `text-muted` |
| `bg-white` | `bg-background` |
| `border-gray-200` | `border-border` |
| `bg-gray-50` | `bg-background` |
| `bg-gray-100` | `bg-background` |
| `hover:bg-blue-100/50` | `hover:bg-surface` |
| `hover:bg-gray-50` | `hover:bg-background` |
| `bg-blue-100 text-green-700` | `bg-primary/10 text-primary` |
| `bg-blue-100 text-blue-700` | `bg-primary/10 text-primary` |
| `bg-yellow-100 text-yellow-700` | `bg-orange-400/10 text-orange-400` |
| `text-gray-400` (chevrons) | `text-muted` |
| `border-blue-200` (in expanded section) | `border-border` |

### 6. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/thread/messages/tool-calls.tsx`
**Replace:**
- `border-gray-200` → `border-border`
- `bg-gray-50` → `bg-background`
- `text-gray-900` → `text-text-main`
- `bg-gray-100` → `bg-background`
- `text-gray-500` → `text-muted`
- `divide-gray-200` → `divide-border`
- `hover:bg-gray-50` → `hover:bg-background`

### 7. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/thread/messages/generic-interrupt.tsx`
**Replace (same pattern as tool-calls.tsx):**
- `border-gray-200` → `border-border`
- `bg-gray-50` → `bg-background`
- `text-gray-900` → `text-text-main`
- `bg-gray-100` → `bg-background`
- `text-gray-500` → `text-muted`
- `divide-gray-200` → `divide-border`
- `hover:bg-gray-50` → `hover:bg-background`
- `text-blue-600` → `text-primary`
- `hover:text-blue-800` → `hover:text-primary`

### 8. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/thread/history/index.tsx`
**Replace:**
- `border-slate-300` → `border-border`
- `bg-gray-300` (scrollbar-thumb) → `bg-border`
- `hover:bg-gray-100` → `hover:bg-surface`

### 9. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/thread/index.tsx`
**Replace textarea className (line ~883):**
- `bg-[#d9e1ee]` → `bg-background`
- `text-[#0f172a]` → `text-text-main`
- `placeholder:text-[#475569]` → `placeholder:text-muted`
- `border-white/60` → `border-border`

### 10. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/thread/agent-inbox/index.tsx`
**Replace:**
- `bg-gray-50` → `bg-surface`
- `border-gray-300` → `border-border`
- `bg-white` → `bg-background`
- `text-gray-600` → `text-muted`

### 11. `/home/mohamed/projects/PI/QoS-Buddy/frontend/app/chat/documents/page.tsx`
**Replace warning banner colors:**
- `bg-amber-50` → `bg-amber-500/10`
- `text-amber-800` → `text-amber-400`
- `border-amber-300` → `border-amber-500/30`

### 12. `/home/mohamed/projects/PI/QoS-Buddy/ai-services/agent/app/graph.py` (line 52)
**Change:**
- `min_score: float = 0.7` → `min_score: float = 0.5`

### 13. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/thread/index.tsx` (line 413)
**Change:**
- `const [minRelevance, setMinRelevance] = useState(0.7);` → `const [minRelevance, setMinRelevance] = useState(0.5);`

### 14. `/home/mohamed/projects/PI/QoS-Buddy/frontend/components/thread/search-settings.tsx`
**Adjust slider range:**
- Change min from `0.5` → `0.3`
- Change max from `0.9` → `0.8`
- Change step from `0.1` → `0.05`
- Update label to show percentage (e.g., "50%" instead of "0.5")

## Execution Order
1. CSS files (markdown-styles.css, chat-theme.css)
2. Shared UI components (badge.tsx, slider.tsx)
3. Core chat components (source-display.tsx, tool-calls.tsx, generic-interrupt.tsx)
4. Layout components (history/index.tsx, thread/index.tsx)
5. Agent inbox components
6. Documents page
7. Relevance score defaults (graph.py, thread/index.tsx)
8. Slider range (search-settings.tsx)
