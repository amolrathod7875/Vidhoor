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
  Download,
  Mail,
  Pencil,
  Trash2,
  Pin,
  PinOff,
  LogIn,
  LogOut,
  X,
} from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { decryptEvidencePayload, encryptFileForUpload } from "@/lib/evidenceCrypto";

interface ChatApiResponse {
  response: string;
  session_id: string;
  masked_entities: Record<string, unknown>;
  citations?: Citation[];
  overall_confidence?: number | null;
  follow_ups?: string[];
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

interface ConnectedDocumentResponse {
  file_name: string;
  relative_path: string;
  size_bytes: number;
  updated_at: string;
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
  citations?: Citation[];
  overall_confidence?: number | null;
  follow_ups?: string[];
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

interface DraftRecordApiResponse {
  draft_id: string;
  user_id: string;
  email_id: string;
  session_id: string;
  application_type: string;
  title: string;
  draft_content: string;
  draft_meta: Record<string, unknown>;
  delivery_status: string;
  last_delivery_error: string;
  emailed_at: string;
  created_at: string;
  updated_at: string;
}

interface DraftUpdateApiRequest {
  title?: string;
  draft_content?: string;
}

interface SessionShareApiResponse {
  share_id: string;
  share_url: string;
  expires_at?: string | null;
}

type DraftFlowState =
  | { step: "idle" }
  | { step: "awaitingType"; sessionId: string }
  | { step: "awaitingFacts"; sessionId: string; applicationType: string };

const SUPPORTED_DRAFT_TYPES = new Set([
  "bail_application",
  "legal_notice",
  "police_complaint",
  "consumer_complaint",
  "custom",
]);

const resolveApiBaseUrl = (): string => {
  const configured = String(import.meta.env.VITE_API_BASE_URL || "").trim();
  if (configured) {
    return configured
      .replace(/\/$/, "")
      .replace(/\/api$/i, "");
  }

  const host = String(window.location.hostname || "").toLowerCase();
  const isLocalHost = host === "localhost" || host === "127.0.0.1";
  if (isLocalHost) {
    return "http://127.0.0.1:8001";
  }

  // On deployed frontend (for example Vercel), rely on same-origin /api rewrites.
  return String(window.location.origin || "").replace(/\/$/, "");
};

const API_BASE_URL = resolveApiBaseUrl();

const resolveLegalDocsBaseUrl = (): string => {
  const configured = String(import.meta.env.VITE_LEGAL_DOCS_BASE_URL || "").trim();
  if (configured) {
    return configured.replace(/\/$/, "");
  }

  if (API_BASE_URL) {
    return API_BASE_URL;
  }

  const host = String(window.location.hostname || "").toLowerCase();
  const isLocalHost = host === "localhost" || host === "127.0.0.1";
  if (isLocalHost) {
    return "http://127.0.0.1:8001";
  }

  return String(window.location.origin || "").replace(/\/$/, "");
};

const LEGAL_DOCS_BASE_URL = resolveLegalDocsBaseUrl();

let nextMessageId = 1;

const createMessageId = (): string => `msg-${nextMessageId++}`;
const createLocalSessionId = (): string => `local-${crypto.randomUUID()}`;

const toPrintableText = (value: string): string => {
  let text = String(value || "").replace(/\r\n/g, "\n");
  text = text.replace(/^#{1,6}\s*/gm, "");
  text = text.replace(/\*\*(.*?)\*\*/g, "$1");
  text = text.replace(/__(.*?)__/g, "$1");
  text = text.replace(/`(.*?)`/g, "$1");
  text = text.replace(/^\s*[-*]\s+/gm, "• ");
  text = text.replace(/\[(?:[A-Z0-9_'\s]+)\]/g, "");
  text = text.replace(/[ \t]+/g, " ");
  text = text.replace(/\n{3,}/g, "\n\n");
  return text.trim();
};

function ChatApp() {
  const { user, logout } = useAuth();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [guestRemaining, setGuestRemaining] = useState(5);
  const [loginOpen, setLoginOpen] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [isUploadingDocument, setIsUploadingDocument] = useState(false);
  const [isGeneratingDraft, setIsGeneratingDraft] = useState(false);
  const [isLoadingDraftHistory, setIsLoadingDraftHistory] = useState(false);
  const [activeDocuments, setActiveDocuments] = useState<ActiveDocumentContext[]>([]);
  const [draftHistory, setDraftHistory] = useState<DraftRecordApiResponse[]>([]);
  const [draftFlow, setDraftFlow] = useState<DraftFlowState>({ step: "idle" });
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [composerText, setComposerText] = useState("");
  const [connectedDocuments, setConnectedDocuments] = useState<ConnectedDocumentResponse[]>([]);
  const [showDraftTile, setShowDraftTile] = useState(true);
  const [draftEditorOpen, setDraftEditorOpen] = useState(false);
  const [isSavingDraftEdit, setIsSavingDraftEdit] = useState(false);
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);
  const [editingDraftTitle, setEditingDraftTitle] = useState("");
  const [editingDraftContent, setEditingDraftContent] = useState("");
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
      setDraftHistory([]);
      setDraftFlow({ step: "idle" });
      prevTempChat.current = tempChat;
    }
  }, [tempChat]);

  useEffect(() => {
    setShowDraftTile(true);
  }, [activeId, tempChat, user?.uid]);

  useEffect(() => {
    const loadConnectedDocuments = async () => {
      try {
        const response = await fetch(`${LEGAL_DOCS_BASE_URL}/api/connected-documents`, {
          method: "GET",
        });

        if (!response.ok) {
          throw new Error(`Failed to load connected documents (${response.status})`);
        }

        const data = (await response.json()) as ConnectedDocumentResponse[];
        setConnectedDocuments(data);
      } catch (error) {
        console.error(error);
        setConnectedDocuments([]);
      }
    };

    void loadConnectedDocuments();
  }, []);

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
          citations: item.citations ?? [],
          overall_confidence: item.overall_confidence ?? null,
          follow_ups: item.follow_ups ?? [],
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

  const fetchSessionDrafts = useCallback(
    async (sessionId: string) => {
      if (!user || tempChat) {
        setDraftHistory([]);
        return;
      }

      setIsLoadingDraftHistory(true);
      try {
        const token = await user.getIdToken();
        const response = await fetch(
          `${API_BASE_URL}/api/drafts?session_id=${encodeURIComponent(sessionId)}`,
          {
            method: "GET",
            headers: {
              Authorization: `Bearer ${token}`,
            },
          }
        );

        if (!response.ok) {
          throw new Error(`Failed to load draft history (${response.status})`);
        }

        const data = (await response.json()) as DraftRecordApiResponse[];
        setDraftHistory(data);
      } catch (error) {
        console.error(error);
        setDraftHistory([]);
      } finally {
        setIsLoadingDraftHistory(false);
      }
    },
    [user, tempChat]
  );

  const addMessage = useCallback(
    (
      role: Message["role"],
      content: string,
      sessionId: string,
      options?: Pick<Message, "citations" | "overall_confidence" | "follow_ups">
    ) => {
      const msg: Message = {
        id: createMessageId(),
        role,
        content,
        citations: options?.citations,
        overall_confidence: options?.overall_confidence,
        follow_ups: options?.follow_ups,
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
    let sid = ensureActiveSession();

    addMessage("user", text, sid);

    if (draftFlow.step !== "idle") {
      if (draftFlow.sessionId !== sid) {
        setDraftFlow({ step: "idle" });
      } else if (draftFlow.step === "awaitingType") {
        const applicationType = normalizeDraftType(text);
        if (!applicationType) {
          addMessage(
            "assistant",
            "Please enter one exact draft type: bail_application, legal_notice, police_complaint, consumer_complaint, or custom.",
            sid
          );
          return;
        }

        setDraftFlow({ step: "awaitingFacts", sessionId: sid, applicationType });
        addMessage(
          "assistant",
          "Now share the key case facts. If you want me to use only existing chat context, reply: use chat context",
          sid
        );
        return;
      } else if (draftFlow.step === "awaitingFacts") {
        const extraFacts = text.trim().toLowerCase() === "use chat context" ? "" : text;
        await generateDraftForSession(sid, draftFlow.applicationType, extraFacts);
        return;
      }
    }

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
        follow_ups: data.follow_ups ?? [],
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

  const handleEditUserMessage = (text: string) => {
    setComposerText(text);
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

  const normalizeDraftType = useCallback((value: string): string | null => {
    const normalized = value.trim().toLowerCase().replace(/\s+/g, "_");
    return SUPPORTED_DRAFT_TYPES.has(normalized) ? normalized : null;
  }, []);

  const ensureActiveSession = useCallback((): string => {
    let sid = activeId;
    if (sid) {
      return sid;
    }

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
    return sid;
  }, [activeId, tempChat]);

  const generateDraftForSession = useCallback(
    async (sessionId: string, applicationType: string, extraFacts: string) => {
      if (!user) {
        setLoginOpen(true);
        return;
      }

      const session = sessions.find((item) => item.id === sessionId) ?? null;
      const caseFacts = [extraFacts.trim(), buildSessionDraftFacts(session)]
        .filter((item) => item.length > 0)
        .join("\n\n")
        .trim();

      if (!caseFacts) {
        addMessage(
          "assistant",
          "I don’t have enough context yet. Please share key case facts in chat so I can prepare the draft.",
          sessionId
        );
        setDraftFlow({ step: "awaitingFacts", sessionId, applicationType });
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
            session_id: sessionId,
            auto_email_to_user: false,
          }),
        });

        if (!response.ok) {
          throw new Error(`Draft generation failed with status ${response.status}`);
        }

        const data = (await response.json()) as DraftGenerateApiResponse;
        const assistantContent = [`### ${data.title}`, data.draft_content, "", `> ${data.disclaimer}`].join("\n");

        addMessage("assistant", assistantContent, sessionId);
        await fetchSessionDrafts(sessionId);
        setDraftFlow({ step: "idle" });
        toast.success("Draft created");
      } catch (error) {
        console.error(error);
        addMessage(
          "assistant",
          "I couldn't generate the legal draft right now. Please retry with clearer facts.",
          sessionId
        );
        toast.error("Draft generation failed");
      } finally {
        setIsTyping(false);
        setIsGeneratingDraft(false);
      }
    },
    [addMessage, buildSessionDraftFacts, fetchSessionDrafts, sessions, user]
  );

  const handleGenerateDraft = async () => {
    if (!user) {
      setLoginOpen(true);
      return;
    }
    if (tempChat) {
      toast.error("Draft documents are disabled in Temporary Chat mode");
      return;
    }

    const sid = ensureActiveSession();
    setDraftFlow({ step: "awaitingType", sessionId: sid });
    addMessage(
      "assistant",
      [
        "Select the draft type by replying with one option:",
        "- bail_application",
        "- legal_notice",
        "- police_complaint",
        "- consumer_complaint",
        "- custom",
      ].join("\n"),
      sid
    );
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
    setComposerText("");
  };

  const handleSelectSession = async (id: string) => {
    setComposerText("");
    setActiveId(id);
    if (!tempChat) {
      await fetchSessionMessages(id);
      await fetchSessionDrafts(id);
    }
  };

  const handleDownloadDraft = async (draftId: string, format: "pdf" | "docx") => {
    if (!user) {
      setLoginOpen(true);
      return;
    }

    try {
      const token = await user.getIdToken();
      const response = await fetch(
        `${API_BASE_URL}/api/drafts/${encodeURIComponent(draftId)}/export?format=${format}`,
        {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
          },
        }
      );

      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(errorBody?.detail || `Draft export failed (${response.status})`);
      }

      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename=([^;]+)/i);
      const fallbackName = `draft.${format}`;
      const fileName = (match?.[1] || fallbackName).trim().replace(/^"|"$/g, "");

      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
      toast.success(`Draft downloaded as ${format.toUpperCase()}`);
    } catch (error) {
      console.error(error);
      toast.error(error instanceof Error ? error.message : "Could not download draft");
    }
  };

  const handleOpenDraftMailComposer = async (draftId: string) => {
    if (!user) {
      setLoginOpen(true);
      return;
    }

    try {
      const token = await user.getIdToken();
      const response = await fetch(`${API_BASE_URL}/api/drafts/${encodeURIComponent(draftId)}`, {
        method: "GET",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      if (!response.ok) {
        throw new Error(`Could not load draft (${response.status})`);
      }

      const draft = (await response.json()) as DraftRecordApiResponse;
      const subject = toPrintableText(`Vidhoor Draft: ${draft.title}`);
      const body = `${toPrintableText(draft.draft_content)}\n\n--\nThis draft was prepared in Vidhoor and should be reviewed before sending.`;
      const gmailUrl =
        `https://mail.google.com/mail/?view=cm&fs=1&tf=1` +
        `&su=${encodeURIComponent(subject)}` +
        `&body=${encodeURIComponent(body)}`;

      window.open(gmailUrl, "_blank", "noopener,noreferrer");
      toast.success("Gmail compose opened. Fill To/CC/BCC and send.");
    } catch (error) {
      console.error(error);
      const fallback = "mailto:";
      window.open(fallback, "_self");
      toast.error("Could not prefill draft; opened default mail app.");
    }
  };

  const handleEditDraft = async (draftId: string) => {
    if (!user) {
      setLoginOpen(true);
      return;
    }

    try {
      const token = await user.getIdToken();
      const response = await fetch(`${API_BASE_URL}/api/drafts/${encodeURIComponent(draftId)}`, {
        method: "GET",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      if (!response.ok) {
        throw new Error(`Could not load draft (${response.status})`);
      }

      const draft = (await response.json()) as DraftRecordApiResponse;
      setEditingDraftId(draft.draft_id);
      setEditingDraftTitle(draft.title || "Legal Draft");
      setEditingDraftContent(draft.draft_content || "");
      setDraftEditorOpen(true);
    } catch (error) {
      console.error(error);
      toast.error("Could not open draft editor");
    }
  };

  const handleSaveDraftEdits = async () => {
    if (!user || !editingDraftId) {
      return;
    }

    const nextTitle = editingDraftTitle.trim();
    const nextContent = editingDraftContent.trim();
    if (!nextTitle) {
      toast.error("Draft title cannot be empty");
      return;
    }
    if (!nextContent) {
      toast.error("Draft content cannot be empty");
      return;
    }

    setIsSavingDraftEdit(true);
    try {
      const token = await user.getIdToken();
      const payload: DraftUpdateApiRequest = {
        title: nextTitle,
        draft_content: nextContent,
      };

      const response = await fetch(
        `${API_BASE_URL}/api/drafts/${encodeURIComponent(editingDraftId)}`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(payload),
        }
      );

      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(errorBody?.detail || `Could not save draft (${response.status})`);
      }

      const updated = (await response.json()) as DraftRecordApiResponse;
      setDraftHistory((prev) =>
        prev.map((draft) =>
          draft.draft_id === updated.draft_id ? updated : draft
        )
      );

      const targetSessionId = (updated.session_id || activeId || "").trim();
      if (targetSessionId) {
        const updatedAssistantContent = [
          `### ${updated.title}`,
          updated.draft_content,
          "",
          "> Updated draft saved.",
        ].join("\n");
        addMessage("assistant", updatedAssistantContent, targetSessionId);
        await fetchSessionDrafts(targetSessionId);
      }

      setDraftEditorOpen(false);
      toast.success("Draft updated");
    } catch (error) {
      console.error(error);
      toast.error(error instanceof Error ? error.message : "Could not save draft");
    } finally {
      setIsSavingDraftEdit(false);
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

    try {
      let shareUrl = "";

      if (user) {
        const token = await user.getIdToken();
        const response = await fetch(
          `${API_BASE_URL}/api/history/sessions/${id}/share`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${token}`,
              "X-Frontend-Origin": window.location.origin,
            },
          }
        );

        if (!response.ok) {
          throw new Error(`Failed to create share link (${response.status})`);
        }

        const payload = (await response.json()) as SessionShareApiResponse;
        shareUrl = String(payload.share_url || "").trim();
      }

      if (!shareUrl) {
        throw new Error("Unable to generate share URL");
      }

      const shareData = {
        title: session.title,
        text: `Shared conversation: ${session.title}`,
        url: shareUrl,
      };

      if (navigator.share) {
        try {
          await navigator.share(shareData);
          return;
        } catch {
          // If user cancels native share, do not show an error toast.
          return;
        }
      }

      await navigator.clipboard.writeText(shareUrl);
      toast.success("Share link copied to clipboard");
    } catch (error) {
      console.error(error);
      toast.error("Could not create share link");
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

  const openRenameDialog = () => {
    if (!activeSession) return;
    setRenameValue(activeSession.title);
    setRenameDialogOpen(true);
  };

  const submitRenameDialog = () => {
    if (!activeSession) return;
    const nextTitle = renameValue.trim();
    if (!nextTitle || nextTitle === activeSession.title.trim()) {
      return;
    }
    void handleRenameSession(activeSession.id, nextTitle);
    setRenameDialogOpen(false);
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
      <div className="flex h-screen w-full overflow-hidden bg-background">
        <VidhoorSidebar
          sessions={sidebarSessions}
          activeSessionId={activeId}
          onSelectSession={(id) => {
            void handleSelectSession(id);
          }}
          onNewChat={handleNewChat}
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
          connectedDocuments={connectedDocuments}
          legalDocsBaseUrl={LEGAL_DOCS_BASE_URL}
          apiBaseUrl={API_BASE_URL}
          tempChat={tempChat}
        />

        <div className="flex min-h-0 flex-1 flex-col">
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
                        openRenameDialog();
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

              {!tempChat && user && activeId && showDraftTile && (
                <div className="border-t border-border/40 bg-muted/20 px-3 py-2">
                  <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-3">
                    <p className="text-xs font-medium text-muted-foreground">Documentation</p>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => {
                          void handleGenerateDraft();
                        }}
                        disabled={isGeneratingDraft}
                      >
                        <FileText className="mr-1.5 h-3.5 w-3.5" />
                        {isGeneratingDraft ? "Drafting..." : "Create Draft"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-muted-foreground"
                        onClick={() => setShowDraftTile(false)}
                        title="Close draft section"
                        aria-label="Close draft section"
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                  <div className="mx-auto mt-2 w-full max-w-3xl">
                    {isLoadingDraftHistory ? (
                      <p className="text-xs text-muted-foreground">Loading draft history...</p>
                    ) : draftHistory.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No drafts yet for this chat.</p>
                    ) : (
                      <div className="space-y-1.5">
                        {draftHistory.slice(0, 5).map((draft) => (
                          <div
                            key={draft.draft_id}
                            className="flex items-center justify-between rounded-lg border border-border/50 bg-background/80 px-2.5 py-2"
                          >
                            <div className="min-w-0">
                              <p className="truncate text-xs font-medium text-foreground">{draft.title}</p>
                              <p className="text-[11px] text-muted-foreground">
                                {new Date(draft.created_at).toLocaleString()} • {draft.application_type}
                              </p>
                            </div>
                            <div className="ml-3 flex items-center gap-1">
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 px-2 text-[11px]"
                                onClick={() => {
                                  void handleEditDraft(draft.draft_id);
                                }}
                              >
                                <Pencil className="mr-1 h-3.5 w-3.5" />
                                Edit
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 px-2 text-[11px]"
                                onClick={() => {
                                  void handleDownloadDraft(draft.draft_id, "pdf");
                                }}
                              >
                                <Download className="mr-1 h-3.5 w-3.5" />
                                PDF
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 px-2 text-[11px]"
                                onClick={() => {
                                  void handleDownloadDraft(draft.draft_id, "docx");
                                }}
                              >
                                <Download className="mr-1 h-3.5 w-3.5" />
                                DOCX
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 px-2 text-[11px]"
                                onClick={() => {
                                  void handleOpenDraftMailComposer(draft.draft_id);
                                }}
                              >
                                <Mail className="mr-1 h-3.5 w-3.5" />
                                Mail
                              </Button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
          </header>

          {/* Chat area */}
          <ChatArea
            messages={activeSession?.messages ?? []}
            isTyping={isTyping}
            isHistoryLoading={activeId !== null && loadingSessionId === activeId}
            onChipClick={handleSend}
            onEditUserMessage={handleEditUserMessage}
          />

          {/* Input */}
          <div className="sticky bottom-0 z-10 border-t border-border/40 bg-background/95 backdrop-blur">
            <ChatInput
              onSend={handleSend}
              onUploadFiles={handleUploadFiles}
              value={composerText}
              onValueChange={setComposerText}
              onCreateDraft={() => {
                void handleGenerateDraft();
              }}
              disabled={inputDisabled}
              isUploading={isUploadingDocument}
              isGeneratingDraft={isGeneratingDraft}
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
      </div>

      <LoginModal open={loginOpen} onOpenChange={setLoginOpen} />

      <Dialog
        open={renameDialogOpen}
        onOpenChange={(open) => {
          setRenameDialogOpen(open);
          if (!open) {
            setRenameValue("");
          }
        }}
      >
        <DialogContent className="sm:max-w-md rounded-2xl">
          <DialogHeader>
            <DialogTitle className="text-3xl font-semibold">Rename this chat</DialogTitle>
          </DialogHeader>

          <form
            className="space-y-6"
            onSubmit={(event) => {
              event.preventDefault();
              submitRenameDialog();
            }}
          >
            <Input
              value={renameValue}
              onChange={(event) => setRenameValue(event.target.value)}
              placeholder="Enter chat title"
              autoFocus
              maxLength={120}
              className="h-14 rounded-lg text-3xl tracking-tight"
            />

            <DialogFooter className="gap-2 sm:justify-start sm:space-x-0">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setRenameDialogOpen(false)}
                className="text-lg"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                className="text-lg"
                disabled={
                  !activeSession ||
                  !renameValue.trim() ||
                  renameValue.trim() === activeSession.title.trim()
                }
              >
                Rename
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={draftEditorOpen}
        onOpenChange={(open) => {
          setDraftEditorOpen(open);
          if (!open) {
            setEditingDraftId(null);
            setEditingDraftTitle("");
            setEditingDraftContent("");
          }
        }}
      >
        <DialogContent className="sm:max-w-3xl rounded-2xl">
          <DialogHeader>
            <DialogTitle className="text-xl font-semibold">Edit Draft</DialogTitle>
          </DialogHeader>

          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void handleSaveDraftEdits();
            }}
          >
            <Input
              value={editingDraftTitle}
              onChange={(event) => setEditingDraftTitle(event.target.value)}
              placeholder="Draft title"
              maxLength={120}
              className="h-11 rounded-lg"
            />

            <textarea
              value={editingDraftContent}
              onChange={(event) => setEditingDraftContent(event.target.value)}
              placeholder="Edit draft content"
              rows={16}
              className="w-full resize-y rounded-lg border border-input bg-background px-3 py-2 text-sm leading-6 outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />

            <DialogFooter className="gap-2 sm:justify-end sm:space-x-0">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setDraftEditorOpen(false)}
                disabled={isSavingDraftEdit}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={isSavingDraftEdit || !editingDraftTitle.trim() || !editingDraftContent.trim()}
              >
                {isSavingDraftEdit ? "Saving..." : "Save Draft"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
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
