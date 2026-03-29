# Frontend - Vidhoor Legal Copilot

React + TypeScript frontend for Vidhoor legal chat, evidence context workflows, and draft operations.

## Tech Stack

- Vite + React 18 + TypeScript
- Tailwind CSS + shadcn/ui + Radix UI
- React Markdown (`remark-gfm`) for assistant responses
- Firebase auth integration
- Vitest + Testing Library

## Folder Structure

- `src/main.tsx` — app bootstrap
- `src/App.tsx` — top-level app wrapper
- `src/pages/`
	- `Index.tsx` — main chat app shell and state orchestration
	- `NotFound.tsx` — fallback route
- `src/components/`
	- `ChatArea.tsx` — message rendering, markdown, source modal
	- `ChatInput.tsx` — input, uploads, draft action trigger
	- `VidhoorSidebar.tsx` — session navigation
	- `LoginModal.tsx` — auth modal
	- `NavLink.tsx` — sidebar/navigation helper
	- `ui/` — shared shadcn/Radix UI primitives
- `src/hooks/`
	- `useAuth.tsx`, `useTheme.tsx`, `use-mobile.tsx`, `use-toast.ts`
- `src/lib/`
	- `firebaseConfig.ts` — firebase initialization
	- `evidenceCrypto.ts` — client-side encryption/decryption helpers
	- `utils.ts` — utility helpers
- `src/types/chat.ts` — chat/citation type definitions
- `src/test/` — unit test setup and examples
- `public/` — static assets
- `playwright.config.ts` and `playwright-fixture.ts` — e2e scaffolding

## Setup

```bash
cd frontend
npm install
```

## Run

```bash
npm run dev
```

Default local URL: `http://127.0.0.1:5173`

## Build / Test / Lint

```bash
npm run build
npm run test
npm run lint
```

## Environment Variables

- `VITE_API_BASE_URL` (optional)
	- default fallback in code: `http://127.0.0.1:8001`

## UX Notes Implemented

- Assistant markdown links open in a new tab with safe `rel` attributes.
- Indian Kanoon links appended by backend are shown as blue links in chat markdown.
- Documentation/Create Draft strip includes a close `X` and resets per session/context changes.
