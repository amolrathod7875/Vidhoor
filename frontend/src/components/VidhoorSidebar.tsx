import { useState, useRef, useEffect, KeyboardEvent } from "react";
import {
  Plus,
  Settings,
  User,
  Palette,
  FileText,
  MessageSquareMore,
  MapPin,
  MessageSquare,
  Ghost,
  MoreVertical,
  Trash2,
  Share2,
  Pencil,
  Pin,
  PinOff,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useTheme } from "@/hooks/useTheme";
import { useAuth } from "@/hooks/useAuth";
import { ChatSession } from "@/types/chat";
import { cn } from "@/lib/utils";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface ConnectedDocumentItem {
  file_name: string;
  relative_path: string;
  size_bytes: number;
  updated_at: string;
}

interface Props {
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewChat: () => void;
  onDeleteSession: (id: string) => void;
  onShareSession: (id: string) => void;
  onRenameSession: (id: string, newTitle: string) => void;
  onPinSession: (id: string) => void;
  connectedDocuments: ConnectedDocumentItem[];
  tempChat: boolean;
}

export function VidhoorSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewChat,
  onDeleteSession,
  onShareSession,
  onRenameSession,
  onPinSession,
  connectedDocuments,
  tempChat,
}: Props) {
  const { theme, setTheme } = useTheme();
  const { user } = useAuth();
  const { state } = useSidebar();
  const collapsed = state === "collapsed";
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const editRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId) editRef.current?.focus();
  }, [editingId]);

  const startRename = (id: string, currentTitle: string) => {
    setEditingId(id);
    setEditValue(currentTitle);
  };

  const commitRename = () => {
    if (editingId && editValue.trim()) {
      onRenameSession(editingId, editValue.trim());
    }
    setEditingId(null);
  };

  const handleEditKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter") { e.preventDefault(); commitRename(); }
    if (e.key === "Escape") setEditingId(null);
  };

  const userLabel = user?.displayName || user?.email || "Guest";

  const openFeedback = () => {
    const subject = encodeURIComponent("Vidhoor feedback");
    window.open(`mailto:support@vidhoor.ai?subject=${subject}`, "_self");
  };

  return (
    <Sidebar collapsible="icon" className="border-r-0">
      <SidebarHeader className="p-3">
        <Button
          onClick={onNewChat}
          className={cn(
            "w-full justify-start gap-2.5 rounded-2xl bg-primary text-primary-foreground hover:bg-primary/90 transition-all active:scale-[0.97]",
            collapsed && "justify-center px-0"
          )}
        >
          <Plus className="h-4 w-4 shrink-0" />
          {!collapsed && <span className="text-sm font-medium">New Chat</span>}
        </Button>
      </SidebarHeader>

      <SidebarContent className="px-2">
        {tempChat ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-12 text-center">
            {!collapsed && (
              <>
                <Ghost className="h-10 w-10 text-muted-foreground/40" />
                <p className="text-xs leading-relaxed text-muted-foreground/60">
                  History is hidden during Temporary Chat.
                </p>
              </>
            )}
            {collapsed && (
              <Ghost className="h-5 w-5 text-muted-foreground/40" />
            )}
          </div>
        ) : (
          <ScrollArea className="flex-1">
            <div className="space-y-0.5 py-2">
              {sessions.map((s) => (
                <div
                  key={s.id}
                  className={cn(
                    "group flex w-full items-center rounded-xl transition-colors",
                    "hover:bg-accent",
                    activeSessionId === s.id
                      ? "bg-accent font-medium text-accent-foreground"
                      : "text-muted-foreground",
                    collapsed && "justify-center"
                  )}
                >
                  <button
                    onClick={() => onSelectSession(s.id)}
                    onDoubleClick={() => !collapsed && startRename(s.id, s.title)}
                    className={cn(
                      "flex flex-1 items-center gap-2.5 px-3 py-2.5 text-left text-sm min-w-0",
                      collapsed && "justify-center px-2"
                    )}
                  >
                    <MessageSquare className="h-4 w-4 shrink-0 opacity-60" />
                    {!collapsed && (
                      editingId === s.id ? (
                        <input
                          ref={editRef}
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          onBlur={commitRename}
                          onKeyDown={handleEditKeyDown}
                          className="flex-1 min-w-0 bg-transparent text-sm outline-none ring-1 ring-primary/40 rounded px-1 py-0.5"
                          onClick={(e) => e.stopPropagation()}
                        />
                      ) : (
                        <div className="flex min-w-0 items-center gap-1.5">
                          {s.pinned && <Pin className="h-3 w-3 shrink-0 text-primary" />}
                          <span className="truncate">{s.title}</span>
                        </div>
                      )
                    )}
                  </button>

                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        aria-label={`Chat options for ${s.title}`}
                        title="Chat options"
                        onClick={(event) => event.stopPropagation()}
                        className={cn(
                          "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-foreground/80 transition-colors hover:bg-background/70 hover:text-foreground focus-visible:text-foreground",
                          collapsed ? "mr-0.5" : "mr-1.5"
                        )}
                      >
                        <MoreVertical className="h-4 w-4" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                      align={collapsed ? "end" : "start"}
                      side="right"
                      className="w-40 rounded-xl"
                    >
                      <DropdownMenuItem
                        onSelect={() => startRename(s.id, s.title)}
                        className="gap-2 rounded-lg text-sm"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                        Rename
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => {
                          if (window.confirm("Delete this chat?")) {
                            onDeleteSession(s.id);
                          }
                        }}
                        className="gap-2 rounded-lg text-sm text-destructive focus:text-destructive"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        Delete
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => onShareSession(s.id)}
                        className="gap-2 rounded-lg text-sm"
                      >
                        <Share2 className="h-3.5 w-3.5" />
                        Share
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => onPinSession(s.id)}
                        className="gap-2 rounded-lg text-sm"
                      >
                        {s.pinned ? (
                          <>
                            <PinOff className="h-3.5 w-3.5" />
                            Unpin
                          </>
                        ) : (
                          <>
                            <Pin className="h-3.5 w-3.5" />
                            Pin
                          </>
                        )}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              ))}
            </div>
          </ScrollArea>
        )}
      </SidebarContent>

      <SidebarFooter className="mt-auto p-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className={cn(
                "h-9 w-9 rounded-xl text-muted-foreground hover:text-foreground",
                collapsed ? "mx-auto" : "ml-1"
              )}
              title="Settings"
              aria-label="Open settings"
            >
              <Settings className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" side="top" className="w-80 rounded-2xl p-2">
            <DropdownMenuLabel className="text-xs uppercase tracking-wide text-muted-foreground">
              Account
            </DropdownMenuLabel>
            <div className="mb-1 flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm">
              <User className="h-4 w-4 text-muted-foreground" />
              <span className="truncate">{userLabel}</span>
            </div>

            <DropdownMenuSeparator />

            <DropdownMenuLabel className="text-xs uppercase tracking-wide text-muted-foreground">
              Appearance
            </DropdownMenuLabel>
            <div className="rounded-lg px-1 pb-1">
              <DropdownMenuRadioGroup value={theme} onValueChange={(value) => setTheme(value as "light" | "dark" | "system")}>
                <DropdownMenuRadioItem value="light" className="gap-2 rounded-md">
                  <Palette className="h-3.5 w-3.5" />
                  Light
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="dark" className="gap-2 rounded-md">
                  <Palette className="h-3.5 w-3.5" />
                  Dark
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="system" className="gap-2 rounded-md">
                  <Palette className="h-3.5 w-3.5" />
                  System
                </DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </div>

            <DropdownMenuSeparator />

            <DropdownMenuLabel className="text-xs uppercase tracking-wide text-muted-foreground">
              Connected Documents
            </DropdownMenuLabel>
            <div className="max-h-44 overflow-y-auto rounded-lg border border-border/50 bg-muted/20 p-1.5">
              {connectedDocuments.length === 0 ? (
                <p className="px-2 py-1 text-xs text-muted-foreground">No documents found.</p>
              ) : (
                connectedDocuments.map((doc) => (
                  <div
                    key={doc.relative_path}
                    className="flex items-start gap-2 rounded-md px-2 py-1.5 text-xs text-foreground/90"
                    title={doc.relative_path}
                  >
                    <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <p className="truncate font-medium">{doc.file_name}</p>
                      <p className="truncate text-muted-foreground">{doc.relative_path}</p>
                    </div>
                  </div>
                ))
              )}
            </div>

            <DropdownMenuSeparator />

            <DropdownMenuItem onSelect={openFeedback} className="gap-2 rounded-lg text-sm">
              <MessageSquareMore className="h-4 w-4" />
              Send feedback
            </DropdownMenuItem>

            <div className="mt-1 rounded-lg border border-border/50 px-2 py-2 text-xs text-muted-foreground">
              <div className="mb-1 flex items-center gap-1.5 text-foreground/80">
                <MapPin className="h-3.5 w-3.5" />
                Location
              </div>
              <p>Update location from browser or device settings.</p>
            </div>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarFooter>
    </Sidebar>
  );
}
