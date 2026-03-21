import { useRef, useEffect, useState } from "react";
import { Message } from "@/types/chat";
import { Scale, Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

const PROMPT_CHIPS = [
  "Draft a bail application",
  "Explain BNS Section 302",
  "Consumer complaint format",
  "IPR patent infringement basics",
];

interface Props {
  messages: Message[];
  isTyping: boolean;
  onChipClick?: (text: string) => void;
}

export function ChatArea({ messages, isTyping, onChipClick }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  const handleCopy = async (id: string, content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedId(id);
      toast.success("Response copied");
      setTimeout(() => setCopiedId((current) => (current === id ? null : current)), 1500);
    } catch {
      toast.error("Could not copy response");
    }
  };

  if (messages.length === 0 && !isTyping) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-8 px-8">
        <h1
          className="max-w-2xl text-center text-4xl font-semibold tracking-tight sm:text-5xl lg:text-6xl animate-fade-in-up"
          style={{
            background:
              "linear-gradient(135deg, hsl(var(--primary)), hsl(var(--primary) / 0.6), hsl(var(--primary) / 0.4))",
            WebkitBackgroundClip: "text",
            WebkitTextFillColor: "transparent",
            lineHeight: 1.15,
          }}
        >
          Hello. How can Vidhoor assist with your case today?
        </h1>
        <div className="flex flex-wrap justify-center gap-2 animate-fade-in-up" style={{ animationDelay: "150ms" }}>
          {PROMPT_CHIPS.map((chip) => (
            <button
              key={chip}
              onClick={() => onChipClick?.(chip)}
              className="rounded-2xl border border-border/60 bg-card px-4 py-2 text-sm text-muted-foreground transition-all hover:border-primary/40 hover:text-foreground hover:shadow-sm active:scale-[0.97]"
            >
              {chip}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto px-4 py-6">
      <div className="mx-auto w-full max-w-3xl space-y-5">
        {messages.map((msg, i) => (
          <div
            key={msg.id}
            className={cn(
              "flex gap-3 animate-fade-in-up",
              msg.role === "user" ? "justify-end" : "justify-start"
            )}
            style={{ animationDelay: `${Math.min(i * 60, 300)}ms` }}
          >
            {msg.role === "assistant" && (
              <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/15">
                <Scale className="h-3.5 w-3.5 text-primary" />
              </div>
            )}
            <div
              className={cn(
                "max-w-[80%] whitespace-pre-wrap text-[15px] leading-relaxed",
                msg.role === "user"
                  ? "rounded-2xl bg-chat-bubble px-4 py-3 text-chat-bubble-foreground"
                  : "pt-0.5 text-foreground"
              )}
            >
              {msg.content}
              {msg.role === "assistant" && (
                <div className="mt-2 flex justify-end">
                  <button
                    onClick={() => handleCopy(msg.id, msg.content)}
                    className="inline-flex items-center gap-1 rounded-md border border-border/60 px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  >
                    {copiedId === msg.id ? (
                      <Check className="h-3.5 w-3.5" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                    {copiedId === msg.id ? "Copied" : "Copy"}
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}

        {isTyping && (
          <div className="flex items-start gap-3 animate-fade-in-up">
            <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/15">
              <Scale className="h-3.5 w-3.5 text-primary" />
            </div>
            <div className="flex items-center gap-1.5 pt-2">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}
