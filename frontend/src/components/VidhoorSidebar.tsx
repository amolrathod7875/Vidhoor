import { useState, useRef, useEffect, KeyboardEvent } from "react";
import {
  Plus,
  Sun,
  Moon,
  LogIn,
  MessageSquare,
  LogOut,
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
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";

interface Props {
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewChat: () => void;
  onLoginClick: () => void;
  onDeleteSession: (id: string) => void;
  onShareSession: (id: string) => void;
  onRenameSession: (id: string, newTitle: string) => void;
  onPinSession: (id: string) => void;
  tempChat: boolean;
}

export function VidhoorSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewChat,
  onLoginClick,
  onDeleteSession,
  onShareSession,
  onRenameSession,
  onPinSession,
  tempChat,
}: Props) {
  const { theme, toggle } = useTheme();
  const { user, logout } = useAuth();
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

  const initials = user?.displayName
    ? user.displayName
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : user?.email?.[0]?.toUpperCase() ?? "U";

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

      <SidebarFooter className="space-y-1 p-3">
        {/* Theme toggle */}
        <Button
          variant="ghost"
          size="sm"
          onClick={toggle}
          className={cn(
            "w-full justify-start gap-2.5 rounded-xl transition-colors active:scale-[0.97]",
            collapsed && "justify-center px-0"
          )}
        >
          {theme === "light" ? (
            <Moon className="h-4 w-4 shrink-0" />
          ) : (
            <Sun className="h-4 w-4 shrink-0" />
          )}
          {!collapsed && (
            <span className="text-sm">
              {theme === "light" ? "Dark mode" : "Light mode"}
            </span>
          )}
        </Button>

        {/* Auth section */}
        {user ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className={cn(
                  "w-full justify-start gap-2.5 rounded-xl transition-colors active:scale-[0.97]",
                  collapsed && "justify-center px-0"
                )}
              >
                <Avatar className="h-6 w-6">
                  <AvatarImage src={user.photoURL ?? undefined} />
                  <AvatarFallback className="text-[10px] bg-primary/15 text-primary">
                    {initials}
                  </AvatarFallback>
                </Avatar>
                {!collapsed && (
                  <span className="truncate text-sm">
                    {user.displayName || user.email}
                  </span>
                )}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-56 rounded-xl">
              <div className="px-3 py-2">
                <p className="text-sm font-medium truncate">
                  {user.displayName || "User"}
                </p>
                <p className="text-xs text-muted-foreground truncate">
                  {user.email}
                </p>
              </div>
              <DropdownMenuItem
                onClick={logout}
                className="gap-2 rounded-lg text-destructive focus:text-destructive"
              >
                <LogOut className="h-4 w-4" />
                Sign Out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            onClick={onLoginClick}
            className={cn(
              "w-full justify-start gap-2.5 rounded-xl transition-colors active:scale-[0.97]",
              collapsed && "justify-center px-0"
            )}
          >
            <LogIn className="h-4 w-4 shrink-0" />
            {!collapsed && <span className="text-sm">Login / Sign Up</span>}
          </Button>
        )}
      </SidebarFooter>
    </Sidebar>
  );
}
