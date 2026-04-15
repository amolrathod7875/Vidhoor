export interface Citation {
  doc_id: string;
  title: string;
  source: string;
  source_url?: string;
  section?: string;
  page?: number | null;
  snippet: string;
  confidence: number;
  last_updated?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  overall_confidence?: number | null;
  follow_ups?: string[];
}

export interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
  pinned?: boolean;
}
