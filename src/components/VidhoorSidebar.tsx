import { Plus, Sun, Moon, LogIn, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useTheme } from "@/hooks/useTheme";
import { ChatSession } from "@/types/chat";
import { cn } from "@/lib/utils";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  useSidebar,
} from "@/components/ui/sidebar";

interface Props {
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewChat: () => void;
  onLoginClick: () => void;
}

export function VidhoorSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewChat,
  onLoginClick,
}: Props) {
  const { theme, toggle } = useTheme();
  const { state } = useSidebar();
  const collapsed = state === "collapsed";

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
                {!collapsed && (
                  <span className="truncate">{s.title}</span>
                )}
              </button>
            ))}
          </div>
        </ScrollArea>
      </SidebarContent>

      <SidebarFooter className="space-y-1 p-3">
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
      </SidebarFooter>
    </Sidebar>
  );
}
