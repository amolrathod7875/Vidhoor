import { useState, useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { VidhoorSidebar } from "@/components/VidhoorSidebar";
import { ChatArea } from "@/components/ChatArea";
import { ChatInput } from "@/components/ChatInput";
import { LoginModal } from "@/components/LoginModal";
import { ThemeProvider } from "@/hooks/useTheme";
import { AuthProvider, useAuth } from "@/hooks/useAuth";
import { ChatSession, Message, Citation } from "@/types/chat";
import {
  Ghost,
  ShieldAlert,
  Share2,
  MoreVertical,
  FileText,
  Pencil,
  Trash2,
  Pin,
  PinOff,
  LogIn,
  LogOut,
} from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { decryptEvidencePayload, encryptFileForUpload } from "@/lib/evidenceCrypto";

interface ChatApiResponse {
  response: string;
  session_id: string;
  masked_entities: Record<string, unknown>;
  citations?: Citation[];
  overall_confidence?: number | null;
}

interface OCRAnalyzeApiResponse {
  response: string;
  summary: string;
  extracted_pages: Array<{
    page: number;
    detected_language: string;
    text_en: string;
  }>;
  citations?: Citation[];
  overall_confidence?: number | null;
  masked_entities: Record<string, unknown>;
  evidence_id?: string | null;
  encrypted_stored?: boolean;
}

interface ActiveDocumentContext {
  id: string;
  name: string;
  type: "image" | "document";
  contextText: string;
  fileExtension?: string;
  encryptedPayloadB64?: string;
  ivB64?: string;
}

interface EvidenceSummaryResponse {
  evidence_id: string;
  file_name: string;
  file_extension: string;
  encryption_alg: string;
  key_id: string;
  session_id: string;
  created_at: string;
}

interface EvidencePayloadResponse {
  evidence_id: string;
  file_name: string;
  file_extension: string;
  encryption_alg: string;
  key_id: string;
  iv_b64: string;
  encrypted_payload_b64: string;
  masked_summary: string;
  masked_analysis: string;
  session_id: string;
  created_at: string;
}

const MAX_ACTIVE_DOCUMENTS = 5;

interface HistorySessionResponse {
  session_id: string;
  title: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

interface HistoryMessageResponse {
  role: "user" | "assistant";
  content: string;
  created_at: string;
  masked_entities: Record<string, string>;
}

interface DraftGenerateApiResponse {
  draft_id: string;
  title: string;
  application_type: string;
  draft_content: string;
  disclaimer: string;
  email_target?: string | null;
  email_sent: boolean;
  email_message: string;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8001";

let nextMessageId = 1;

const createMessageId = (): string => `msg-${nextMessageId++}`;
const createLocalSessionId = (): string => `local-${crypto.randomUUID()}`;

function ChatApp() {
  const { user, logout } = useAuth();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [guestRemaining, setGuestRemaining] = useState(5);
  const [loginOpen, setLoginOpen] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [isUploadingDocument, setIsUploadingDocument] = useState(false);
  const [isGeneratingDraft, setIsGeneratingDraft] = useState(false);
  const [activeDocuments, setActiveDocuments] = useState<ActiveDocumentContext[]>([]);
  const [tempChat, setTempChat] = useState(false);
  const [loadingSessionId, setLoadingSessionId] = useState<string | null>(null);
  const prevTempChat = useRef(tempChat);
  const loadedHistorySessionIds = useRef<Set<string>>(new Set());

  const activeSession = sessions.find((s) => s.id === activeId) ?? null;

  const sortSessions = useCallback((list: ChatSession[]) => {
    const pinned = list.filter((item) => item.pinned);
    const unpinned = list.filter((item) => !item.pinned);
    return [...pinned, ...unpinned];
  }, []);

  // Clear active chat when tempChat toggled
  useEffect(() => {
    if (prevTempChat.current !== tempChat) {
      setActiveId(null);
      setActiveDocuments([]);
      prevTempChat.current = tempChat;
    }
  }, [tempChat]);

  useEffect(() => {
    const loadHistorySessions = async () => {
      if (!user) {
        return;
      }

      try {
        const token = await user.getIdToken();
        const response = await fetch(`${API_BASE_URL}/api/history/sessions`, {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });

        if (!response.ok) {
          throw new Error(`Failed to load sessions (${response.status})`);
        }

        const data = (await response.json()) as HistorySessionResponse[];
        const mappedSessions: ChatSession[] = data.map((item) => ({
          id: item.session_id,
          title: item.title,
          messages: [],
          pinned: item.pinned,
        }));

        if (mappedSessions.length > 0) {
          setSessions(sortSessions(mappedSessions));
        }
      } catch (error) {
        console.error(error);
        toast.error("Could not load chat history");
      }
    };

    void loadHistorySessions();
  }, [user, sortSessions]);

  useEffect(() => {
    const loadActiveSessionEvidence = async () => {
      if (!user || !activeId || tempChat) {
        return;
      }

      try {
        const token = await user.getIdToken();
        const listResponse = await fetch(
          `${API_BASE_URL}/api/evidence?session_id=${encodeURIComponent(activeId)}`,
          {
            method: "GET",
            headers: {
              Authorization: `Bearer ${token}`,
            },
          }
        );

        if (!listResponse.ok) {
          throw new Error(`Failed to load evidence list (${listResponse.status})`);
        }

        const listData = (await listResponse.json()) as EvidenceSummaryResponse[];
        const sessionEvidence = listData.slice(0, MAX_ACTIVE_DOCUMENTS);

        if (sessionEvidence.length === 0) {
          setActiveDocuments([]);
          return;
        }

        const detailResponses = await Promise.all(
          sessionEvidence.map(async (item) => {
            const detailResponse = await fetch(
              `${API_BASE_URL}/api/evidence/${item.evidence_id}`,
              {
                method: "GET",
                headers: {
                  Authorization: `Bearer ${token}`,
                },
              }
            );

            if (!detailResponse.ok) {
              throw new Error(
                `Failed to load evidence payload ${item.evidence_id} (${detailResponse.status})`
              );
            }

            return (await detailResponse.json()) as EvidencePayloadResponse;
          })
        );

        const imageExtensions = new Set([".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"]);

        setActiveDocuments(
          detailResponses.map((item) => ({
            id: item.evidence_id,
            name: item.file_name,
            type: imageExtensions.has((item.file_extension || "").toLowerCase())
              ? "image"
              : "document",
            contextText: `${item.masked_summary}\n\n${item.masked_analysis}`.trim(),
            fileExtension: item.file_extension,
            encryptedPayloadB64: item.encrypted_payload_b64,
            ivB64: item.iv_b64,
          }))
        );
      } catch (error) {
        console.error(error);
      }
    };

    void loadActiveSessionEvidence();
  }, [activeId, user, tempChat]);

  const fetchSessionMessages = useCallback(
    async (sessionId: string) => {
      if (!user) {
        return;
      }
      if (loadedHistorySessionIds.current.has(sessionId)) {
        return;
      }

      setLoadingSessionId(sessionId);
      try {
        const token = await user.getIdToken();
        const response = await fetch(
          `${API_BASE_URL}/api/history/sessions/${sessionId}`,
          {
            method: "GET",
            headers: {
              Authorization: `Bearer ${token}`,
            },
          }
        );

        if (!response.ok) {
          throw new Error(`Failed to load session messages (${response.status})`);
        }

        const data = (await response.json()) as HistoryMessageResponse[];
        const mappedMessages: Message[] = data.map((item) => ({
          id: createMessageId(),
          role: item.role,
          content: item.content,
        }));

        setSessions((prev) =>
          prev.map((session) =>
            session.id === sessionId
              ? { ...session, messages: mappedMessages }
              : session
          )
        );

        loadedHistorySessionIds.current.add(sessionId);
      } catch (error) {
        console.error(error);
        toast.error("Could not load selected chat");
      } finally {
        setLoadingSessionId(null);
      }
    },
    [user]
  );

  const addMessage = useCallback(
    (
      role: Message["role"],
      content: string,
      sessionId: string,
      options?: Pick<Message, "citations" | "overall_confidence">
    ) => {
      const msg: Message = {
        id: createMessageId(),
        role,
        content,
        citations: options?.citations,
        overall_confidence: options?.overall_confidence,
      };
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

  const handleSend = async (text: string) => {
    let sid = activeId;

    if (!sid) {
      const newSession: ChatSession = {
        id: createLocalSessionId(),
        title: "New Chat",
        messages: [],
        pinned: false,
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

    // Typing indicator then backend response
    setIsTyping(true);
    try {
      const token = user ? await user.getIdToken() : null;
      const response = await fetch(`${API_BASE_URL}/api/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          message: text,
          session_id: sid,
          is_temporary_chat: tempChat,
          document_context: activeDocuments[0]?.contextText,
          document_name: activeDocuments[0]?.name,
          document_contexts: activeDocuments.map((doc) => doc.contextText),
          document_names: activeDocuments.map((doc) => doc.name),
        }),
      });

      if (!response.ok) {
        throw new Error(`Backend request failed with status ${response.status}`);
      }

      const data = (await response.json()) as ChatApiResponse;

      if (data.session_id && data.session_id !== sid) {
        setSessions((prev) =>
          prev.map((session) =>
            session.id === sid ? { ...session, id: data.session_id } : session
          )
        );
        sid = data.session_id;
        setActiveId(sid);
      }

      if (user && !tempChat) {
        loadedHistorySessionIds.current.add(sid);
      }
      addMessage("assistant", data.response, sid, {
        citations: data.citations ?? [],
        overall_confidence: data.overall_confidence ?? null,
      });
    } catch (error) {
      console.error(error);
      addMessage(
        "assistant",
        "I could not reach the Vidhoor backend right now. Please try again.",
        sid
      );
      toast.error("Backend connection failed");
    } finally {
      setIsTyping(false);
    }
  };

  const buildSessionDraftFacts = useCallback((session: ChatSession | null): string => {
    if (!session || session.messages.length === 0) {
      return "";
    }

    const recent = session.messages.slice(-10);
    return recent
      .map((item) => `${item.role === "user" ? "User" : "Vidhoor"}: ${item.content}`)
      .join("\n\n")
      .trim();
  }, []);

  const handleGenerateDraft = async () => {
    if (!user) {
      setLoginOpen(true);
      return;
    }

    const requestedType =
      window.prompt(
        "Draft type? (bail_application, legal_notice, police_complaint, consumer_complaint, custom)",
        "bail_application"
      ) ?? "bail_application";
    const normalizedType = requestedType.trim().toLowerCase().replace(/\s+/g, "_");
    const supportedTypes = new Set([
      "bail_application",
      "legal_notice",
      "police_complaint",
      "consumer_complaint",
      "custom",
    ]);
    const applicationType = supportedTypes.has(normalizedType) ? normalizedType : "custom";

    const extraFacts =
      window.prompt(
        "Add any key facts for this draft (optional). Leave blank to use recent chat context only.",
        ""
      ) ?? "";

    let sid = activeId;
    if (!sid) {
      const newSession: ChatSession = {
        id: createLocalSessionId(),
        title: "New Chat",
        messages: [],
        pinned: false,
      };

      if (tempChat) {
        setSessions((prev) => [{ ...newSession, title: "⌛ " + newSession.title }, ...prev]);
      } else {
        setSessions((prev) => [newSession, ...prev]);
      }

      sid = newSession.id;
      setActiveId(sid);
    }

    const session = sessions.find((item) => item.id === sid) ?? null;
    const caseFacts = [extraFacts.trim(), buildSessionDraftFacts(session)]
      .filter((item) => item.length > 0)
      .join("\n\n")
      .trim();

    if (!caseFacts) {
      toast.error("Please provide case facts or add some chat context first");
      return;
    }

    setIsGeneratingDraft(true);
    setIsTyping(true);

    try {
      const token = await user.getIdToken();
      const response = await fetch(`${API_BASE_URL}/api/drafts/generate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          "X-User-Email": user.email ?? "",
        },
        body: JSON.stringify({
          application_type: applicationType,
          case_facts: caseFacts,
          session_id: sid,
          auto_email_to_user: true,
        }),
      });

      if (!response.ok) {
        throw new Error(`Draft generation failed with status ${response.status}`);
      }

      const data = (await response.json()) as DraftGenerateApiResponse;
      const assistantContent = [
        `### ${data.title}`,
        data.draft_content,
        "",
        `> ${data.disclaimer}`,
        "",
        data.email_message,
      ].join("\n");

      addMessage("assistant", assistantContent, sid);
      toast.success(data.email_sent ? "Draft created and mailed" : "Draft created");
    } catch (error) {
      console.error(error);
      addMessage(
        "assistant",
        "I couldn't generate the legal draft right now. Please retry with clearer facts.",
        sid
      );
      toast.error("Draft generation failed");
    } finally {
      setIsTyping(false);
      setIsGeneratingDraft(false);
    }
  };

  const handleUploadFiles = async (files: File[]) => {
    if (files.length === 0) {
      return;
    }

    let sid = activeId;

    if (!sid) {
      const newSession: ChatSession = {
        id: createLocalSessionId(),
        title: "New Chat",
        messages: [],
        pinned: false,
      };

      if (tempChat) {
        setSessions((prev) => [{ ...newSession, title: "⌛ " + newSession.title }, ...prev]);
      } else {
        setSessions((prev) => [newSession, ...prev]);
      }

      sid = newSession.id;
      setActiveId(sid);
    }

    const availableSlots = Math.max(0, MAX_ACTIVE_DOCUMENTS - activeDocuments.length);
    if (availableSlots <= 0) {
      toast.error("You can upload up to 5 files only");
      return;
    }

    const filesToUpload = files.slice(0, availableSlots);
    if (files.length > availableSlots) {
      toast.error(`Only ${availableSlots} more file(s) can be uploaded`);
    }

    setIsUploadingDocument(true);
    setIsTyping(true);

    try {
      const token = user ? await user.getIdToken() : null;
      for (const file of filesToUpload) {
        addMessage("user", `Uploaded document: ${file.name}`, sid);
        const encryptedUpload = await encryptFileForUpload(file);
        const formData = new FormData();
        formData.append("file", file);
        formData.append("encrypted_payload_b64", encryptedUpload.encryptedPayloadB64);
        formData.append("iv_b64", encryptedUpload.ivB64);
        formData.append("encryption_alg", encryptedUpload.encryptionAlg);
        formData.append("key_id", encryptedUpload.keyId);
        formData.append("session_id", sid);
        formData.append("is_temporary_chat", String(tempChat));

        const response = await fetch(`${API_BASE_URL}/api/fir/analyze`, {
          method: "POST",
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: formData,
        });

        if (!response.ok) {
          throw new Error(`Document analysis failed with status ${response.status}`);
        }

        const data = (await response.json()) as OCRAnalyzeApiResponse;
        const extractedContext = data.extracted_pages
          .map((page) => `[Page ${page.page}]\n${page.text_en}`)
          .join("\n\n")
          .trim();
        const isImage = file.type.startsWith("image/");

        setActiveDocuments((prev) => [
          ...prev,
          {
            id: data.evidence_id || `evidence-${createMessageId()}`,
            name: file.name,
            type: isImage ? "image" : "document",
            contextText: extractedContext || data.summary,
            fileExtension: (file.name.match(/\.[^.]+$/)?.[0] || "").toLowerCase(),
            encryptedPayloadB64: encryptedUpload.encryptedPayloadB64,
            ivB64: encryptedUpload.ivB64,
          },
        ]);

        const assistantContent = [
          "### OCR Summary",
          data.summary,
          "",
          "### Legal Analysis",
          data.response,
          "",
          data.encrypted_stored
            ? "🔐 Encrypted evidence saved for your account."
            : "🔐 Encrypted evidence generated client-side.",
        ].join("\n");

        if (!user || tempChat) {
          addMessage("assistant", assistantContent, sid, {
            citations: data.citations ?? [],
            overall_confidence: data.overall_confidence ?? null,
          });
        } else {
          await fetchSessionMessages(sid);
        }
      }
    } catch (error) {
      console.error(error);
      addMessage(
        "assistant",
        "I could not process this document right now. Please try again with a clearer scan or a supported file type.",
        sid
      );
      toast.error("Document processing failed");
    } finally {
      setIsTyping(false);
      setIsUploadingDocument(false);
    }
  };

  const handleNewChat = () => {
    setActiveId(null);
    setActiveDocuments([]);
  };

  const handleSelectSession = async (id: string) => {
    setActiveId(id);
    if (!tempChat) {
      await fetchSessionMessages(id);
    }
  };

  const getMimeTypeFromExtension = (extension: string | undefined): string => {
    const normalized = (extension || "").toLowerCase();
    switch (normalized) {
      case ".pdf":
        return "application/pdf";
      case ".png":
        return "image/png";
      case ".jpg":
      case ".jpeg":
        return "image/jpeg";
      case ".webp":
        return "image/webp";
      case ".bmp":
        return "image/bmp";
      case ".tiff":
        return "image/tiff";
      case ".txt":
        return "text/plain";
      case ".doc":
        return "application/msword";
      case ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
      case ".rtf":
        return "application/rtf";
      default:
        return "application/octet-stream";
    }
  };

  const handleOpenActiveDocument = async (documentId: string) => {
    const target = activeDocuments.find((item) => item.id === documentId);
    if (!target) {
      return;
    }

    try {
      let encryptedPayloadB64 = target.encryptedPayloadB64;
      let ivB64 = target.ivB64;
      let fileExtension = target.fileExtension;

      if ((!encryptedPayloadB64 || !ivB64) && user) {
        const token = await user.getIdToken();
        const detailResponse = await fetch(`${API_BASE_URL}/api/evidence/${documentId}`, {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });

        if (!detailResponse.ok) {
          throw new Error(`Failed to fetch encrypted resource (${detailResponse.status})`);
        }

        const detail = (await detailResponse.json()) as EvidencePayloadResponse;
        encryptedPayloadB64 = detail.encrypted_payload_b64;
        ivB64 = detail.iv_b64;
        fileExtension = detail.file_extension;
      }

      if (!encryptedPayloadB64 || !ivB64) {
        toast.error("Encrypted resource payload unavailable for this file");
        return;
      }

      const decryptedBlob = await decryptEvidencePayload(encryptedPayloadB64, ivB64);
      const typedBlob = new Blob([decryptedBlob], { type: getMimeTypeFromExtension(fileExtension) });
      const objectUrl = URL.createObjectURL(typedBlob);
      window.open(objectUrl, "_blank", "noopener,noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    } catch (error) {
      console.error(error);
      toast.error("Could not open this resource. Use the same browser profile used for upload.");
    }
  };

  const handleDeleteSession = async (id: string) => {
    try {
      if (user) {
        const token = await user.getIdToken();
        const response = await fetch(`${API_BASE_URL}/api/history/sessions/${id}`, {
          method: "DELETE",
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });

        if (!response.ok && response.status !== 404) {
          throw new Error(`Failed to delete session (${response.status})`);
        }
      }

      setSessions((prev) => prev.filter((s) => s.id !== id));
      loadedHistorySessionIds.current.delete(id);
      if (activeId === id) {
        setActiveId(null);
      }
      toast.success("Chat deleted");
    } catch (error) {
      console.error(error);
      toast.error("Could not delete chat");
    }
  };

  const handlePinSession = async (id: string) => {
    const target = sessions.find((session) => session.id === id);
    if (!target) {
      return;
    }

    const nextPinned = !target.pinned;

    try {
      if (user) {
        const token = await user.getIdToken();
        const response = await fetch(`${API_BASE_URL}/api/history/sessions/${id}`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ pinned: nextPinned }),
        });

        if (!response.ok) {
          throw new Error(`Failed to pin/unpin session (${response.status})`);
        }
      }

      setSessions((prev) => {
        const updated = prev.map((session) =>
          session.id === id ? { ...session, pinned: nextPinned } : session
        );
        return sortSessions(updated);
      });
      toast.success(nextPinned ? "Chat pinned" : "Chat unpinned");
    } catch (error) {
      console.error(error);
      toast.error("Could not update pin state");
    }
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

  const handleRenameSession = async (id: string, newTitle: string) => {
    try {
      if (user) {
        const token = await user.getIdToken();
        const response = await fetch(`${API_BASE_URL}/api/history/sessions/${id}`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ title: newTitle }),
        });

        if (!response.ok) {
          throw new Error(`Failed to rename session (${response.status})`);
        }
      }

      setSessions((prev) =>
        prev.map((s) => (s.id === id ? { ...s, title: newTitle } : s))
      );
      toast.success("Chat renamed");
    } catch (error) {
      console.error(error);
      toast.error("Could not rename chat");
    }
  };

  // Filter out temp sessions from sidebar display
  const sidebarSessions = sessions.filter(
    (s) => !s.title.startsWith("⌛ ")
  );

  const inputDisabled = !user && guestRemaining <= 0;
  const userInitials = user?.displayName
    ? user.displayName
        .split(" ")
        .map((part) => part[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : user?.email?.[0]?.toUpperCase() ?? "G";

  return (
    <SidebarProvider>
      <div className="flex min-h-screen w-full bg-background">
        <VidhoorSidebar
          sessions={sidebarSessions}
          activeSessionId={activeId}
          onSelectSession={(id) => {
            void handleSelectSession(id);
          }}
          onNewChat={handleNewChat}
          onLoginClick={() => setLoginOpen(true)}
          onDeleteSession={(id) => {
            void handleDeleteSession(id);
          }}
          onShareSession={handleShareSession}
          onRenameSession={(id, newTitle) => {
            void handleRenameSession(id, newTitle);
          }}
          onPinSession={(id) => {
            void handlePinSession(id);
          }}
          tempChat={tempChat}
        />

        <div className="flex flex-1 flex-col">
          {/* Header */}
          <header className="flex flex-col border-b border-border/40">
            <div className="relative flex h-14 items-center px-3">
              <div className="z-10 flex items-center gap-2">
                <SidebarTrigger />
                <button
                  onClick={handleNewChat}
                  className="hidden text-sm font-medium text-foreground/75 transition-opacity hover:opacity-80 sm:inline"
                >
                  Vidhoor
                </button>
              </div>

              <div className="absolute inset-x-20 text-center">
                <button
                  type="button"
                  onClick={handleNewChat}
                  className="max-w-full truncate bg-transparent px-0 py-0 text-base font-semibold text-foreground outline-none transition-opacity hover:opacity-80"
                >
                  {activeSession?.title || "New Chat"}
                </button>
              </div>

              <div className="z-10 ml-auto flex items-center gap-1">
                <div className="mr-1 hidden items-center gap-2 sm:flex">
                  <Ghost className="h-4 w-4 text-muted-foreground" />
                  <Switch
                    checked={tempChat}
                    onCheckedChange={setTempChat}
                    className="data-[state=checked]:bg-primary"
                  />
                </div>

                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => {
                    if (activeSession) {
                      void handleShareSession(activeSession.id);
                    }
                  }}
                  disabled={!activeSession}
                  className="h-9 w-9 rounded-full"
                >
                  <Share2 className="h-4 w-4" />
                </Button>

                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={!activeSession && !user}
                      className="h-9 w-9 rounded-full"
                    >
                      <MoreVertical className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-40 rounded-xl">
                    <DropdownMenuItem
                      onSelect={() => {
                        void handleGenerateDraft();
                      }}
                      className="gap-2"
                      disabled={!user || isGeneratingDraft}
                    >
                      <FileText className="h-3.5 w-3.5" />
                      {isGeneratingDraft ? "Drafting..." : "Draft Application"}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onSelect={() => {
                        if (!activeSession) return;
                        const updatedTitle = window.prompt("Rename chat", activeSession.title);
                        if (updatedTitle && updatedTitle.trim()) {
                          void handleRenameSession(activeSession.id, updatedTitle.trim());
                        }
                      }}
                      className="gap-2"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      Rename
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => {
                        if (activeSession) {
                          void handleDeleteSession(activeSession.id);
                        }
                      }}
                      className="gap-2 text-destructive focus:text-destructive"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      Delete
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => {
                        if (activeSession) {
                          void handlePinSession(activeSession.id);
                        }
                      }}
                      className="gap-2"
                    >
                      {activeSession?.pinned ? (
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

                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon" className="h-9 w-9 rounded-full">
                      <Avatar className="h-7 w-7">
                        <AvatarImage src={user?.photoURL ?? undefined} />
                        <AvatarFallback className="bg-primary/15 text-primary text-[10px]">
                          {userInitials}
                        </AvatarFallback>
                      </Avatar>
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-56 rounded-xl">
                    {user ? (
                      <>
                        <div className="px-3 py-2">
                          <p className="truncate text-sm font-medium">{user.displayName || "User"}</p>
                          <p className="truncate text-xs text-muted-foreground">{user.email}</p>
                        </div>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          onSelect={() => {
                            void logout();
                          }}
                          className="gap-2 text-destructive focus:text-destructive"
                        >
                          <LogOut className="h-4 w-4" />
                          Sign Out
                        </DropdownMenuItem>
                      </>
                    ) : (
                      <DropdownMenuItem
                        onSelect={() => setLoginOpen(true)}
                        className="gap-2"
                      >
                        <LogIn className="h-4 w-4" />
                        Login / Sign Up
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
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
            isHistoryLoading={activeId !== null && loadingSessionId === activeId}
            onChipClick={handleSend}
          />

          {/* Input */}
          <ChatInput
            onSend={handleSend}
            onUploadFiles={handleUploadFiles}
            disabled={inputDisabled}
            isUploading={isUploadingDocument}
            guestRemaining={guestRemaining}
            activeDocuments={activeDocuments}
            onRemoveActiveDocument={(documentId) =>
              setActiveDocuments((prev) =>
                prev.filter((document) => document.id !== documentId)
              )
            }
            onOpenActiveDocument={handleOpenActiveDocument}
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
