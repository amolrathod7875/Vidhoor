import { useState, useCallback } from "react";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { VidhoorSidebar } from "@/components/VidhoorSidebar";
import { ChatArea } from "@/components/ChatArea";
import { ChatInput } from "@/components/ChatInput";
import { LoginModal } from "@/components/LoginModal";
import { ThemeProvider } from "@/hooks/useTheme";
import { ChatSession, Message } from "@/types/chat";

const SAMPLE_SESSIONS: ChatSession[] = [
  { id: "1", title: "Breach of Contract - Tata Motors", messages: [] },
  { id: "2", title: "BNS Section 480 Bail Application", messages: [] },
  { id: "3", title: "IPR Infringement - Pharma Patent", messages: [] },
  { id: "4", title: "Consumer Dispute - E-commerce", messages: [] },
];

const AI_RESPONSES = [
  "Based on my analysis of the relevant statutes and case law, here are the key points to consider for your case:\n\n1. The burden of proof lies with the plaintiff to establish a prima facie case.\n2. Under Section 73 of the Indian Contract Act, damages must be foreseeable and proximate.\n3. Recent precedent from the Supreme Court in *Mahanagar Telephone Nigam Ltd v. Applied Electronics* supports the position that consequential damages are recoverable.\n\nWould you like me to draft a more detailed legal memo on any of these points?",
  "I've reviewed the relevant provisions. Section 480 of the Bharatiya Nyaya Sanhita deals with counterfeiting of currency. For a bail application, you'll want to argue:\n\n• The accused has deep roots in the community\n• No flight risk exists given the circumstances\n• The investigation is substantially complete\n\nShall I help draft the bail application?",
  "Under the Consumer Protection Act, 2019, an e-commerce entity is liable for any deficiency in services. The key steps would be:\n\n1. File a complaint before the District Consumer Commission\n2. Attach all documentary evidence — invoices, correspondence, screenshots\n3. Seek compensation under Section 39\n\nI can help you structure the complaint if needed.",
];

let nextId = 10;

export default function Index() {
  const [sessions, setSessions] = useState<ChatSession[]>(SAMPLE_SESSIONS);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [guestRemaining, setGuestRemaining] = useState(5);
  const [loginOpen, setLoginOpen] = useState(false);

  const activeSession = sessions.find((s) => s.id === activeId) ?? null;

  const addMessage = useCallback(
    (role: Message["role"], content: string, sessionId: string) => {
      const msg: Message = { id: String(nextId++), role, content };
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId ? { ...s, messages: [...s.messages, msg] } : s
        )
      );
    },
    []
  );

  const handleSend = (text: string) => {
    let sid = activeId;
    if (!sid) {
      const newSession: ChatSession = {
        id: String(nextId++),
        title: text.length > 40 ? text.slice(0, 40) + "…" : text,
        messages: [],
      };
      setSessions((prev) => [newSession, ...prev]);
      sid = newSession.id;
      setActiveId(sid);
    }

    addMessage("user", text, sid);

    // Decrement guest counter
    const newRemaining = guestRemaining - 1;
    setGuestRemaining(newRemaining);

    // Simulate AI response
    const responseIdx = Math.floor(Math.random() * AI_RESPONSES.length);
    setTimeout(() => {
      addMessage("assistant", AI_RESPONSES[responseIdx], sid!);
    }, 800);

    if (newRemaining <= 0) {
      setTimeout(() => setLoginOpen(true), 1200);
    }
  };

  const handleNewChat = () => setActiveId(null);

  return (
    <ThemeProvider>
      <SidebarProvider>
        <div className="flex min-h-screen w-full bg-background">
          <VidhoorSidebar
            sessions={sessions}
            activeSessionId={activeId}
            onSelectSession={setActiveId}
            onNewChat={handleNewChat}
            onLoginClick={() => setLoginOpen(true)}
          />

          <div className="flex flex-1 flex-col">
            {/* Header with sidebar trigger */}
            <header className="flex h-12 items-center border-b border-border/40 px-3">
              <SidebarTrigger />
              <span className="ml-3 text-sm font-medium text-foreground/70">Vidhoor</span>
            </header>

            {/* Chat messages */}
            <ChatArea messages={activeSession?.messages ?? []} />

            {/* Input area */}
            <ChatInput
              onSend={handleSend}
              disabled={guestRemaining <= 0}
              guestRemaining={guestRemaining}
            />
          </div>
        </div>

        <LoginModal open={loginOpen} onOpenChange={setLoginOpen} />
      </SidebarProvider>
    </ThemeProvider>
  );
}
