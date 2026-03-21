import { useState, useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { VidhoorSidebar } from "@/components/VidhoorSidebar";
import { ChatArea } from "@/components/ChatArea";
import { ChatInput } from "@/components/ChatInput";
import { LoginModal } from "@/components/LoginModal";
import { ThemeProvider } from "@/hooks/useTheme";
import { AuthProvider, useAuth } from "@/hooks/useAuth";
import { ChatSession, Message } from "@/types/chat";
import { Ghost, EyeOff, ShieldAlert } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

const SAMPLE_SESSIONS: ChatSession[] = [
  { id: "1", title: "Breach of Contract - Tata Motors", messages: [] },
  { id: "2", title: "BNS Section 480 Bail Application", messages: [] },
  { id: "3", title: "IPR Infringement - Pharma Patent", messages: [] },
  { id: "4", title: "Consumer Dispute - E-commerce", messages: [] },
];

const AI_RESPONSES = [
  "Based on my analysis of the relevant statutes and case law, here are the key points to consider:\n\n1. The burden of proof lies with the plaintiff to establish a prima facie case.\n2. Under Section 73 of the Indian Contract Act, damages must be foreseeable and proximate.\n3. Recent precedent from the Supreme Court in *Mahanagar Telephone Nigam Ltd v. Applied Electronics* supports the position that consequential damages are recoverable.\n\nWould you like me to draft a more detailed legal memo on any of these points?",
  "I've reviewed the relevant provisions. Section 480 of the Bharatiya Nyaya Sanhita deals with counterfeiting of currency. For a bail application, you'll want to argue:\n\n• The accused has deep roots in the community\n• No flight risk exists given the circumstances\n• The investigation is substantially complete\n\nShall I help draft the bail application?",
  "Under the Consumer Protection Act, 2019, an e-commerce entity is liable for any deficiency in services. The key steps would be:\n\n1. File a complaint before the District Consumer Commission\n2. Attach all documentary evidence — invoices, correspondence, screenshots\n3. Seek compensation under Section 39\n\nI can help you structure the complaint if needed.",
];

let nextId = 100;

function ChatApp() {
  const { user } = useAuth();
  const [sessions, setSessions] = useState<ChatSession[]>(SAMPLE_SESSIONS);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [guestRemaining, setGuestRemaining] = useState(5);
  const [loginOpen, setLoginOpen] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [tempChat, setTempChat] = useState(false);
  const prevTempChat = useRef(tempChat);

  const activeSession = sessions.find((s) => s.id === activeId) ?? null;

  // Clear active chat when tempChat toggled
  useEffect(() => {
    if (prevTempChat.current !== tempChat) {
      setActiveId(null);
      prevTempChat.current = tempChat;
    }
  }, [tempChat]);

  const addMessage = useCallback(
    (role: Message["role"], content: string, sessionId: string) => {
      const msg: Message = { id: String(nextId++), role, content };
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId
            ? { ...s, messages: [...s.messages, msg] }
            : s
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

      if (tempChat) {
        // Temporary chat — keep in state but mark as hidden from sidebar
        setSessions((prev) => [
          { ...newSession, title: "⌛ " + newSession.title },
          ...prev,
        ]);
      } else {
        setSessions((prev) => [newSession, ...prev]);
      }

      sid = newSession.id;
      setActiveId(sid);
    }

    addMessage("user", text, sid);

    // Guest mode: decrement counter only when unauthenticated
    if (!user) {
      const newRemaining = guestRemaining - 1;
      setGuestRemaining(newRemaining);

      if (newRemaining <= 0) {
        setTimeout(() => setLoginOpen(true), 2000);
      }
    }

    // Typing indicator then response
    setIsTyping(true);
    const responseIdx = Math.floor(Math.random() * AI_RESPONSES.length);
    setTimeout(() => {
      setIsTyping(false);
      addMessage("assistant", AI_RESPONSES[responseIdx], sid!);
    }, 1500);
  };

  const handleNewChat = () => {
    setActiveId(null);
  };

  const handleDeleteSession = (id: string) => {
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (activeId === id) setActiveId(null);
    toast.success("Chat deleted");
  };

  const handleShareSession = async (id: string) => {
    const session = sessions.find((s) => s.id === id);
    if (!session) return;

    const text = session.messages
      .map((m) => `${m.role === "user" ? "You" : "Vidhoor"}: ${m.content}`)
      .join("\n\n");

    const shareData = { title: session.title, text };

    if (navigator.share) {
      try {
        await navigator.share(shareData);
      } catch {
        /* user cancelled */
      }
    } else {
      await navigator.clipboard.writeText(
        `${session.title}\n\n${text || "(empty chat)"}`
      );
      toast.success("Chat copied to clipboard");
    }
  };

  // Filter out temp sessions from sidebar display
  const sidebarSessions = sessions.filter(
    (s) => !s.title.startsWith("⌛ ")
  );

  const inputDisabled = !user && guestRemaining <= 0;

  return (
    <SidebarProvider>
      <div className="flex min-h-screen w-full bg-background">
        <VidhoorSidebar
          sessions={sidebarSessions}
          activeSessionId={activeId}
          onSelectSession={setActiveId}
          onNewChat={handleNewChat}
          onLoginClick={() => setLoginOpen(true)}
          tempChat={tempChat}
        />

        <div className="flex flex-1 flex-col">
          {/* Header */}
          <header className="flex flex-col border-b border-border/40">
            <div className="flex h-12 items-center justify-between px-3">
              <div className="flex items-center gap-2">
                <SidebarTrigger />
                <span className="text-sm font-medium text-foreground/70">
                  Vidhoor
                </span>
              </div>

              {/* Temporary Chat toggle */}
              <div className="flex items-center gap-2">
                <Ghost className="h-4 w-4 text-muted-foreground" />
                <span className="hidden text-xs text-muted-foreground sm:inline">
                  Temporary
                </span>
                <Switch
                  checked={tempChat}
                  onCheckedChange={setTempChat}
                  className="data-[state=checked]:bg-primary"
                />
              </div>
            </div>

            {/* Temp chat warning banner */}
            {tempChat && (
              <div className="flex items-center justify-center gap-2 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary animate-fade-in-up">
                <ShieldAlert className="h-3.5 w-3.5" />
                Temporary Chat: This conversation will not be saved.
              </div>
            )}
          </header>

          {/* Chat area */}
          <ChatArea
            messages={activeSession?.messages ?? []}
            isTyping={isTyping}
            onChipClick={handleSend}
          />

          {/* Input */}
          <ChatInput
            onSend={handleSend}
            disabled={inputDisabled}
            guestRemaining={guestRemaining}
          />
        </div>
      </div>

      <LoginModal open={loginOpen} onOpenChange={setLoginOpen} />
    </SidebarProvider>
  );
}

export default function Index() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <ChatApp />
      </AuthProvider>
    </ThemeProvider>
  );
}
