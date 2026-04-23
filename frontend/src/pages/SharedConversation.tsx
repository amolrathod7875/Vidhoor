import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { MessageSquare, ArrowLeft } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface SharedMessage {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

interface SharedSessionResponse {
  session_id: string;
  title: string;
  messages: SharedMessage[];
  created_at: string;
  updated_at: string;
}

const resolveApiBaseUrl = (): string => {
  const configured = String(import.meta.env.VITE_API_BASE_URL || "").trim();
  if (configured) {
    return configured.replace(/\/$/, "").replace(/\/api$/i, "");
  }

  const host = String(window.location.hostname || "").toLowerCase();
  const isLocalHost = host === "localhost" || host === "127.0.0.1";
  if (isLocalHost) {
    return "http://127.0.0.1:8001";
  }

  return String(window.location.origin || "").replace(/\/$/, "");
};

const API_BASE_URL = resolveApiBaseUrl();

const SMALL_TALK_PATTERNS = [
  /^(hi|hello|hey)\b/i,
  /how can i assist you today\??/i,
  /are you looking for information on a particular topic\??/i,
  /you'?ve initiated a conversation with me/i,
  /what'?s on your mind\??/i,
];

const isLikelySmallTalk = (text: string): boolean => {
  const value = String(text || "").trim();
  if (!value) {
    return true;
  }

  if (value.length <= 8 && /^(hi|hey|hello|yo)$/i.test(value)) {
    return true;
  }

  return SMALL_TALK_PATTERNS.some((pattern) => pattern.test(value));
};

const SharedConversation = () => {
  const { shareId } = useParams<{ shareId: string }>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [payload, setPayload] = useState<SharedSessionResponse | null>(null);

  useEffect(() => {
    const loadSharedSession = async () => {
      if (!shareId) {
        setError("Invalid share link.");
        setLoading(false);
        return;
      }

      try {
        const response = await fetch(`${API_BASE_URL}/api/shared/${encodeURIComponent(shareId)}`);
        if (!response.ok) {
          if (response.status === 410) {
            throw new Error("This share link has expired.");
          }
          if (response.status === 404) {
            throw new Error("Shared conversation not found.");
          }
          throw new Error("Failed to load shared conversation.");
        }

        const data = (await response.json()) as SharedSessionResponse;
        setPayload(data);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load shared conversation.";
        setError(message);
      } finally {
        setLoading(false);
      }
    };

    void loadSharedSession();
  }, [shareId]);

  const updatedLabel = useMemo(() => {
    if (!payload?.updated_at) {
      return "";
    }

    const timestamp = new Date(payload.updated_at);
    if (Number.isNaN(timestamp.getTime())) {
      return "";
    }

    return timestamp.toLocaleString();
  }, [payload?.updated_at]);

  const visibleMessages = useMemo(() => {
    const all = payload?.messages || [];
    if (all.length === 0) {
      return [];
    }

    const filtered = all.filter((item) => !isLikelySmallTalk(item.content));
    return filtered.length > 0 ? filtered : all;
  }, [payload?.messages]);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <div className="mx-auto max-w-4xl px-4 py-10">
          <p className="text-sm text-slate-600">Loading shared conversation...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <div className="mx-auto max-w-4xl px-4 py-10">
          <p className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p>
          <Link
            to="/"
            className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-slate-700 hover:text-slate-900"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Vidhoor
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-4xl px-4 py-8 sm:py-10">
        <div className="mb-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-start gap-3">
            <div className="rounded-full bg-slate-100 p-2 text-slate-600">
              <MessageSquare className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-slate-900">{payload?.title || "Shared Conversation"}</h1>
              {updatedLabel ? (
                <p className="mt-1 text-xs text-slate-500">Last updated: {updatedLabel}</p>
              ) : null}
            </div>
          </div>
        </div>

        <div className="space-y-3">
          {visibleMessages.map((message, index) => {
            const isUser = message.role === "user";
            return (
              <div
                key={`${message.created_at}-${index}`}
                className={`rounded-2xl border p-4 shadow-sm ${
                  isUser
                    ? "border-blue-200 bg-blue-50"
                    : "border-slate-200 bg-white"
                }`}
              >
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {isUser ? "User" : "Vidhoor"}
                </p>
                {isUser ? (
                  <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800">{message.content}</p>
                ) : (
                  <div className="prose prose-sm max-w-none text-slate-800 prose-headings:my-2 prose-p:my-2 prose-ul:my-2 prose-ol:my-2 prose-li:my-1">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        a: ({ node, href, ...props }) => (
                          <a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-700 underline underline-offset-2 hover:text-blue-800"
                            {...props}
                          />
                        ),
                        h2: ({ node, ...props }) => (
                          <h2 className="mt-4 text-lg font-bold leading-snug" {...props} />
                        ),
                        h3: ({ node, ...props }) => (
                          <h3 className="mt-3 text-base font-bold leading-snug" {...props} />
                        ),
                      }}
                    >
                      {message.content}
                    </ReactMarkdown>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default SharedConversation;
