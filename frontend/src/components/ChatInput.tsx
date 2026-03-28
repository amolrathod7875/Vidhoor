import { useRef, useState, KeyboardEvent, ChangeEvent } from "react";
import { ArrowUp, FileText, ImageIcon, Paperclip, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import TextareaAutosize from "react-textarea-autosize";
import { useAuth } from "@/hooks/useAuth";

interface Props {
  onSend: (text: string) => void;
  onUploadFiles: (files: File[]) => void;
  onCreateDraft?: () => void;
  disabled: boolean;
  isUploading?: boolean;
  isGeneratingDraft?: boolean;
  guestRemaining: number;
  activeDocuments?: Array<{
    id: string;
    name: string;
    type: "image" | "document";
  }>;
  onRemoveActiveDocument?: (id: string) => void;
  onOpenActiveDocument?: (id: string) => void;
}

const MAX_ACTIVE_DOCUMENTS = 5;

export function ChatInput({
  onSend,
  onUploadFiles,
  onCreateDraft,
  disabled,
  isUploading = false,
  isGeneratingDraft = false,
  guestRemaining,
  activeDocuments = [],
  onRemoveActiveDocument,
  onOpenActiveDocument,
}: Props) {
  const [text, setText] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { user } = useAuth();

  const isBlocked = !user && disabled;

  const openFilePicker = (uploadType: "image" | "document") => {
    if (isBlocked || isUploading || !fileInputRef.current) {
      return;
    }

    fileInputRef.current.accept =
      uploadType === "image"
        ? "image/*"
        : ".pdf,.doc,.docx,.txt,.rtf";
    fileInputRef.current.click();
  };

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || isBlocked) return;
    onSend(trimmed);
    setText("");
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileSelect = (event: ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(event.target.files ?? []);
    if (!selectedFiles.length || isBlocked || isUploading) {
      return;
    }

    const remainingSlots = Math.max(0, MAX_ACTIVE_DOCUMENTS - activeDocuments.length);
    if (remainingSlots <= 0) {
      window.alert("You can upload up to 5 files only.");
      event.target.value = "";
      return;
    }

    const filesToUpload = selectedFiles.slice(0, remainingSlots);
    if (selectedFiles.length > remainingSlots) {
      window.alert(`Only ${remainingSlots} more file(s) can be uploaded.`);
    }

    onUploadFiles(filesToUpload);
    event.target.value = "";
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      {/* Guest counter pill — only show when not authenticated */}
      {!user && (
        <div className="mb-3 flex justify-center">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/50 px-3.5 py-1 text-xs text-muted-foreground backdrop-blur-sm">
            Guest Mode: {guestRemaining}/5 free queries remaining
          </span>
        </div>
      )}

      {activeDocuments.length > 0 && (
        <div className="mb-2 space-y-2">
          {activeDocuments.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center justify-between rounded-2xl border border-border/60 bg-muted/50 px-3 py-2"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span className="rounded-md bg-background/70 p-1 text-muted-foreground">
                  {doc.type === "image" ? (
                    <ImageIcon className="h-3.5 w-3.5" />
                  ) : (
                    <FileText className="h-3.5 w-3.5" />
                  )}
                </span>
                <div className="min-w-0">
                  <button
                    type="button"
                    onClick={() => onOpenActiveDocument?.(doc.id)}
                    className="max-w-full truncate text-left text-sm font-medium text-foreground underline-offset-2 hover:underline"
                    title="Open uploaded resource"
                  >
                    {doc.name}
                  </button>
                  <p className="text-xs text-muted-foreground">
                    {doc.type === "image" ? "Image" : "Document"} attached for grounded chat
                  </p>
                </div>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => onRemoveActiveDocument?.(doc.id)}
                className="h-7 w-7 shrink-0 rounded-lg text-muted-foreground"
                title="Remove uploaded document context"
                disabled={isUploading}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Input box */}
      <div
        className={cn(
          "relative flex items-end rounded-2xl border border-border/50 bg-card shadow-sm transition-all duration-200 focus-within:border-primary/40 focus-within:shadow-md focus-within:ring-1 focus-within:ring-primary/20",
          isBlocked && "opacity-50"
        )}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,.pdf,.doc,.docx,.txt,.rtf"
          className="hidden"
          onChange={handleFileSelect}
        />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="icon"
              type="button"
              variant="ghost"
              disabled={isBlocked || isUploading || isGeneratingDraft}
              className="m-1.5 h-8 w-8 shrink-0 rounded-xl text-muted-foreground"
              title="Upload file"
            >
              <Paperclip className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" side="top" className="w-36">
            <DropdownMenuItem onSelect={() => openFilePicker("image")} className="gap-2">
              <ImageIcon className="h-3.5 w-3.5" />
              Upload image
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => openFilePicker("document")} className="gap-2">
              <FileText className="h-3.5 w-3.5" />
              Upload document
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => onCreateDraft?.()}
              disabled={!onCreateDraft || isGeneratingDraft}
              className="gap-2"
            >
              <FileText className="h-3.5 w-3.5" />
              {isGeneratingDraft ? "Drafting..." : "Draft document"}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <TextareaAutosize
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isBlocked || isUploading}
          placeholder={
            isBlocked
              ? "Sign in to continue…"
              : isUploading
                ? "Processing uploaded document…"
                : activeDocuments.length > 0
                  ? "Ask questions about the uploaded documents…"
                  : "Ask Vidhoor a legal question…"
          }
          minRows={1}
          maxRows={5}
          className="flex-1 resize-none bg-transparent px-4 py-3.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed"
        />
        <Button
          size="icon"
          onClick={handleSend}
          disabled={isBlocked || isUploading || !text.trim()}
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
