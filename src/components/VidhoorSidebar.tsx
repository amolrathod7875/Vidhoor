import {
  Plus,
  Sun,
  Moon,
  LogIn,
  MessageSquare,
  LogOut,
  Ghost,
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
  tempChat: boolean;
}

export function VidhoorSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewChat,
  onLoginClick,
  tempChat,
}: Props) {
  const { theme, toggle } = useTheme();
  const { user, logout } = useAuth();
  const { state } = useSidebar();
  const collapsed = state === "collapsed";

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
          /* Temporary chat: hide history, show placeholder */
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
                <button
                  key={s.id}
                  onClick={() => onSelectSession(s.id)}
                  className={cn(
                    "group flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm transition-colors",
                    "hover:bg-accent",
                    activeSessionId === s.id
                      ? "bg-accent font-medium text-accent-foreground"
                      : "text-muted-foreground",
                    collapsed && "justify-center px-2"
                  )}
                >
                  <MessageSquare className="h-4 w-4 shrink-0 opacity-60" />
                  {!collapsed && <span className="truncate">{s.title}</span>}
                </button>
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
