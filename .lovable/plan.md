

# Vidhoor — Legal AI Copilot Chat Interface

## Overview
A modern, Gemini-inspired chat application for a legal AI copilot called "Vidhoor". Two-pane layout with full light/dark mode support, guest query limiting, and a login gate.

## Pages & Components

### 1. Global Layout
- `SidebarProvider` + custom sidebar + main content area
- Dark/light theme toggle using a class-based dark mode with localStorage persistence
- Minimalist design with `rounded-2xl` elements and generous whitespace

### 2. Left Sidebar
- **New Chat** button with Plus icon at top
- **Chat History** list — scrollable, clickable rounded tabs with sample legal session titles (e.g., "Breach of Contract - Tata Motors", "BNS Section 480 Bail")
- **Bottom section**: Theme toggle (Sun/Moon) and "Login / Sign Up" button
- Collapsible via sidebar trigger in the header

### 3. Main Chat Area
- **Empty state**: Large centered gradient text — "Hello. How can Vidhoor assist with your case today?"
- **User messages**: Right-aligned, soft gray bubble (dark: dark gray)
- **AI messages**: Left-aligned, no background, clean text with a small Vidhoor avatar/logo
- Smooth scroll-to-bottom on new messages

### 4. Input Area
- Floating, wide input box docked at bottom center
- Multi-line `<textarea>` that auto-expands
- Send button (ArrowUp icon) inside the input on the right
- **Guest counter pill**: "Guest Mode: X/5 free queries remaining" shown above input

### 5. Footer Disclaimer
- Small muted text below input: "Vidhoor is an AI and can make mistakes. Always verify critical legal information."

### 6. Application Logic
- **Guest mode state**: Counter starts at 5, decrements on each sent message
- When counter reaches 0: input is disabled, login modal auto-appears
- **Login modal**: Clean dialog with Email, Password fields, "Sign In" button, and "Create Account" link
- Chat history sidebar items switch between conversations (local state, no backend)
- AI responses will be simulated placeholder text for now

### Tech
- React + Tailwind CSS + shadcn/ui components (Dialog, Button, Input, Sheet/Sidebar)
- Lucide React icons throughout
- All client-side state (no backend needed initially)

