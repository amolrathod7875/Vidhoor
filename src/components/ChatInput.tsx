import { useState, KeyboardEvent } from "react";
import { ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import TextareaAutosize from "react-textarea-autosize";

interface Props {
  onSend: (text: string) => void;
  disabled: boolean;
  guestRemaining: number;
}

export function ChatInput({ onSend, disabled, guestRemaining }: Props) {
  const [text, setText] = useState("");

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      {/* Guest counter pill */}
      <div className="mb-3 flex justify-center">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/50 px-3.5 py-1 text-xs text-muted-foreground backdrop-blur-sm">
          Guest Mode: {guestRemaining}/5 free queries remaining
        </span>
      </div>

      {/* Input box */}
      <div
        className={cn(
          "relative flex items-end rounded-2xl border border-border/50 bg-card shadow-sm transition-all duration-200 focus-within:border-primary/40 focus-within:shadow-md focus-within:ring-1 focus-within:ring-primary/20",
          disabled && "opacity-50"
        )}
      >
        <TextareaAutosize
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={
            disabled
              ? "Sign in to continue…"
              : "Ask Vidhoor a legal question…"
          }
          minRows={1}
          maxRows={5}
          className="flex-1 resize-none bg-transparent px-4 py-3.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed"
        />
        <Button
          size="icon"
          onClick={handleSend}
          disabled={disabled || !text.trim()}
          className="m-1.5 h-8 w-8 shrink-0 rounded-xl transition-all active:scale-95"
        >
          <ArrowUp className="h-4 w-4" />
        </Button>
      </div>

      {/* Disclaimer */}
      <p className="mt-2.5 text-center text-[11px] text-muted-foreground/60">
        Vidhoor is an AI and can make mistakes. Always verify critical legal
        information.
      </p>
    </div>
  );
}
