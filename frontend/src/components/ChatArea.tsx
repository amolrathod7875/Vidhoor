import { useRef, useEffect, useState } from "react";
import { Message } from "@/types/chat";
import { Scale, Copy, Check } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useTheme } from "@/hooks/useTheme";
import HorizontalLoadingBarDark from "@/components/ui/horizontal_loading_bar_dark";
import HorizontalLoadingBarLight from "@/components/ui/horizontal_loading_bar_light";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const PROMPT_CHIPS = [
  "Draft a bail application",
  "Explain BNS Section 302",
  "Consumer complaint format",
  "IPR patent infringement basics",
];

const formatConfidence = (value?: number | null): string => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "N/A";
  }
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
};

const formatDate = (raw?: string): string => {
  if (!raw) {
    return "N/A";
  }

  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return raw;
  }

  return parsed.toLocaleDateString();
};

interface Props {
  messages: Message[];
  isTyping: boolean;
  isHistoryLoading: boolean;
  onChipClick?: (text: string) => void;
}

export function ChatArea({
  messages,
  isTyping,
  isHistoryLoading,
  onChipClick,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [activeSourcesMessageId, setActiveSourcesMessageId] = useState<string | null>(null);
  const { theme } = useTheme();

  const activeSourcesMessage =
    messages.find((item) => item.id === activeSourcesMessageId) ?? null;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping, isHistoryLoading]);

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

  if (messages.length === 0 && !isTyping && !isHistoryLoading) {
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
      {isHistoryLoading && (
        <div className="w-full animate-fade-in-up pb-4">
          {theme === "dark" ? (
            <HorizontalLoadingBarDark />
          ) : (
            <HorizontalLoadingBarLight />
          )}
        </div>
      )}

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
                "max-w-[80%] text-[15px] leading-relaxed",
                msg.role === "user"
                  ? "rounded-2xl bg-chat-bubble px-4 py-3 text-chat-bubble-foreground whitespace-pre-wrap"
                  : "pt-0.5 text-foreground"
              )}
            >
              {msg.role === "assistant" ? (
                <div className="prose prose-sm max-w-none whitespace-normal text-foreground dark:prose-invert prose-p:my-2 prose-ul:my-2 prose-ol:my-2 prose-li:my-1 prose-headings:my-2">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      h2: ({ node, ...props }) => (
                        <h2 className="mt-4 text-lg font-bold leading-snug" {...props} />
                      ),
                      h3: ({ node, ...props }) => (
                        <h3 className="mt-3 text-base font-bold leading-snug" {...props} />
                      ),
                    }}
                  >
                    {msg.content}
                  </ReactMarkdown>
                </div>
              ) : (
                msg.content
              )}
              {msg.role === "assistant" && (
                <div className="mt-2 flex justify-end gap-2">
                  {msg.citations && msg.citations.length > 0 && (
                    <button
                      onClick={() => setActiveSourcesMessageId(msg.id)}
                      className="inline-flex items-center gap-1 rounded-md border border-border/60 px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                    >
                      Sources
                    </button>
                  )}
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

        {isTyping && !isHistoryLoading && (
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

      <Dialog
        open={Boolean(activeSourcesMessageId)}
        onOpenChange={(open) => {
          if (!open) {
            setActiveSourcesMessageId(null);
          }
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Source Material</DialogTitle>
          </DialogHeader>

          <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
            {activeSourcesMessage?.citations?.map((citation, index) => (
              <div
                key={`${activeSourcesMessage.id}-source-${index}`}
                className="rounded-md border border-border/60 bg-card/50 p-3"
              >
                <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">{citation.title}</span>
                  <span>•</span>
                  <span>{citation.section ? `Section ${citation.section}` : "Section N/A"}</span>
                  <span>•</span>
                  <span>{citation.page ? `Page ${citation.page}` : "Page N/A"}</span>
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
                  {citation.snippet}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>Confidence: {formatConfidence(citation.confidence)}</span>
                  <span>•</span>
                  <span>Updated: {formatDate(citation.last_updated)}</span>
                </div>
              </div>
            ))}

            {activeSourcesMessage?.overall_confidence !== null &&
              activeSourcesMessage?.overall_confidence !== undefined && (
                <p className="text-xs text-muted-foreground">
                  Overall confidence: {formatConfidence(activeSourcesMessage.overall_confidence)}
                </p>
              )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
