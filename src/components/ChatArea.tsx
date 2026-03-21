import { useRef, useEffect } from "react";
import { Message } from "@/types/chat";
import { Scale } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  messages: Message[];
}

export function ChatArea({ messages }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-6">
        <h1
          className="text-center text-4xl font-semibold tracking-tight sm:text-5xl md:text-6xl"
          style={{
            background: "linear-gradient(135deg, hsl(220 70% 55%), hsl(280 60% 55%), hsl(340 65% 55%))",
            WebkitBackgroundClip: "text",
            WebkitTextFillColor: "transparent",
            lineHeight: 1.15,
          }}
        >
          Hello. How can Vidhoor assist with your case today?
        </h1>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto px-4 py-6">
      <div className="mx-auto w-full max-w-3xl space-y-6">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={cn(
              "flex gap-3",
              msg.role === "user" ? "justify-end" : "justify-start"
            )}
          >
            {msg.role === "assistant" && (
              <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
                <Scale className="h-4 w-4 text-primary" />
              </div>
            )}
            <div
              className={cn(
                "max-w-[80%] whitespace-pre-wrap text-[15px] leading-relaxed",
                msg.role === "user"
                  ? "rounded-2xl bg-secondary px-4 py-3 text-secondary-foreground"
                  : "pt-1 text-foreground"
              )}
            >
              {msg.content}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
