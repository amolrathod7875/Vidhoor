"""Agentic RAG orchestration with router and judge/answer phases."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AgenticRagConfig:
    max_expansions: int = 3
    max_citations: int = 8
    max_context_chunks: int = 12
    max_context_chars: int = 12000
    max_router_chars: int = 1200
    use_router_llm: bool = True


@dataclass(slots=True)
class AgenticRagHelpers:
    infer_act_filters: Callable[[str], list[str | None]]
    extract_requested_references: Callable[[str], list[str]]
    citation_matches_allowed_acts: Callable[[Any, list[str | None]], bool]
    citation_matches_requested_references: Callable[[Any, list[str]], bool]
    format_citation_context: Callable[[Any], str]
    normalize_citation_links: Callable[[list[Any], Any], list[Any]]
    citation_factory: Callable[[dict[str, Any]], Any]


@dataclass(slots=True)
class AgenticRagResult:
    response: str
    citations: list[Any] = field(default_factory=list)
    overall_confidence: Optional[float] = None
    clarifying_question: Optional[str] = None
    router_used: bool = False


@dataclass(slots=True)
class RouterDecision:
    act_filters: list[str | None]
    expansions: list[str]
    needs_case_law: bool = False
    needs_recent_case_law: bool = False


@dataclass(slots=True)
class JudgeDecision:
    status: str
    final_answer: str | None = None
    clarifying_question: str | None = None


class AgenticRagRunner:
    """Two-step agentic RAG: router -> retrieval -> judge/answer."""

    def __init__(
        self,
        *,
        llm_engine: Any,
        chroma_manager: Any,
        helpers: AgenticRagHelpers,
        config: AgenticRagConfig | None = None,
    ) -> None:
        self._llm_engine = llm_engine
        self._chroma_manager = chroma_manager
        self._helpers = helpers
        self._config = config or AgenticRagConfig()

        self._router_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    (
                        "You are a fast legal query router. "
                        "Return JSON only, no commentary."
                    ),
                ),
                (
                    "human",
                    (
                        "User query:\n{query}\n\n"
                        "Return JSON with this schema and only these keys:\n"
                        "{{\n"
                        "  \"act_filters\": [<canonical act name or null>],\n"
                        "  \"expansions\": [<short query expansion strings>],\n"
                        "  \"needs_case_law\": <true|false>,\n"
                        "  \"needs_recent_case_law\": <true|false>\n"
                        "}}\n\n"
                        "Canonical acts (use exactly):\n"
                        "- Bharatiya Nyaya Sanhita\n"
                        "- Bharatiya Nagarik Suraksha Sanhita\n"
                        "- Bharatiya Sakshya Adhiniyam\n"
                        "- Constitution of India\n"
                        "- Information Technology Act, 2000\n"
                        "- Indian Case Law\n"
                        "Return at most 4 expansions, each under 12 words."
                    ),
                ),
            ]
        )
        self._router_chain = self._router_prompt | self._llm_engine.llm | StrOutputParser()

        self._judge_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    (
                        "You are a legal judge and answer generator. "
                        "Use ONLY the provided legal context. "
                        "If context is insufficient or mismatched, return status=insufficient. "
                        "Return JSON only."
                    ),
                ),
                (
                    "human",
                    (
                        "User query:\n{query}\n\n"
                        "Requested references:\n{requested_refs}\n\n"
                        "Legal context:\n{context}\n\n"
                        "Return JSON with one of these forms:\n"
                        "{{\"status\":\"insufficient\",\"clarifying_question\":\"...\"}}\n"
                        "OR\n"
                        "{{\"status\":\"sufficient\",\"final_answer\":\"...\"}}\n\n"
                        "If you answer, use this exact markdown structure (no extra sections):\n"
                        "## What you can do\n"
                        "- Action or remedy in plain language\n"
                        "  - **Legal basis:** <Act Name> Section <Number>\n"
                        "  - **Why this helps:** short bullet\n\n"
                        "## Laws supporting the above actions\n"
                        "### <Act Name> Section <Number>: Detailed Legal Explanation\n"
                        "- **What the law states:** bullet points only\n"
                        "- **Essential legal ingredients:** bullet points only\n"
                        "- **Punishment or legal consequences:** bullet points only\n"
                        "- **Exceptions, provisos, and defences:** bullet points only\n"
                        "- **Practical application:** bullet points only\n"
                        "- **Limits and uncertainty:** bullet points only\n\n"
                        "## Summary table of applicable laws\n"
                        "| Offence | BNS Section | Description |\n"
                        "| --- | --- | --- |\n"
                        "| <Short offence label> | Section <Number> | <Single-sentence practical use> |"
                    ),
                ),
            ]
        )
        self._judge_chain = self._judge_prompt | self._llm_engine.llm | StrOutputParser()

    def run(
        self,
        *,
        masked_query: str,
        masked_document_context: str,
        request: Any | None = None,
    ) -> AgenticRagResult:
        router_decision, router_used = self._route(masked_query)
        retrieval = self._retrieve(
            masked_query=masked_query,
            masked_document_context=masked_document_context,
            router_decision=router_decision,
            request=request,
        )

        if retrieval["insufficient"]:
            return AgenticRagResult(
                response=retrieval["clarifying_question"],
                citations=[],
                overall_confidence=None,
                clarifying_question=retrieval["clarifying_question"],
                router_used=router_used,
            )

        judge_decision = self._judge(
            masked_query=masked_query,
            requested_refs=retrieval["requested_refs"],
            context_blocks=retrieval["context_blocks"],
        )

        if judge_decision.status != "sufficient" or not judge_decision.final_answer:
            clarifying = judge_decision.clarifying_question or retrieval["clarifying_question"]
            return AgenticRagResult(
                response=clarifying,
                citations=[],
                overall_confidence=None,
                clarifying_question=clarifying,
                router_used=router_used,
            )

        final_answer = self._normalize_answer(judge_decision.final_answer)
        return AgenticRagResult(
            response=final_answer,
            citations=retrieval["citations"],
            overall_confidence=retrieval["overall_confidence"],
            clarifying_question=None,
            router_used=router_used,
        )

    def _route(self, masked_query: str) -> tuple[RouterDecision, bool]:
        normalized_query = " ".join(str(masked_query or "").split())
        base_act_filters = self._helpers.infer_act_filters(normalized_query)
        requested_refs = self._helpers.extract_requested_references(normalized_query)

        expansions = self._build_ref_expansions(requested_refs)

        if self._should_skip_router_llm(base_act_filters, requested_refs):
            return (
                RouterDecision(
                    act_filters=base_act_filters,
                    expansions=expansions,
                    needs_case_law="Indian Case Law" in base_act_filters,
                    needs_recent_case_law=False,
                ),
                False,
            )

        if not self._config.use_router_llm:
            return (
                RouterDecision(
                    act_filters=base_act_filters,
                    expansions=expansions,
                ),
                False,
            )

        router_text = self._router_chain.invoke(
            {
                "query": normalized_query[: self._config.max_router_chars],
            }
        )
        fallback = RouterDecision(
            act_filters=base_act_filters or [None],
            expansions=expansions,
            needs_case_law="Indian Case Law" in base_act_filters,
            needs_recent_case_law=False,
        )
        decision, _ = self._parse_router_output(str(router_text), fallback)
        return (decision, True)

    def _retrieve(
        self,
        *,
        masked_query: str,
        masked_document_context: str,
        router_decision: RouterDecision,
        request: Any | None,
    ) -> dict[str, Any]:
        requested_refs = self._helpers.extract_requested_references(masked_query)
        act_filters = router_decision.act_filters or [None]
        if router_decision.needs_case_law and "Indian Case Law" not in act_filters:
            act_filters.append("Indian Case Law")

        query_variants = [masked_query]
        if masked_document_context:
            query_variants[0] = (
                f"{masked_query}\n\nDocument context (for grounding):\n"
                f"{masked_document_context[:3000]}"
            )

        query_variants.extend(router_decision.expansions)

        focus_query = self._build_focus_query(masked_query)
        if focus_query:
            query_variants.append(focus_query)

        raw_citations: list[dict[str, Any]] = []
        retrieved_context: list[str] = []
        seen_context: set[str] = set()
        seen_citations: set[tuple[str, str]] = set()

        for act_filter in act_filters:
            for query_variant in query_variants:
                retrieval = self._chroma_manager.retrieve_context_with_metadata(
                    query_string=query_variant,
                    filter_status="active",
                    filter_act=act_filter,
                )

                if act_filter and not retrieval.get("documents") and not retrieval.get("citations"):
                    retrieval = self._chroma_manager.retrieve_context_with_metadata(
                        query_string=query_variant,
                        filter_status="active",
                        filter_act=None,
                    )

                for context_chunk in retrieval.get("documents", []):
                    chunk_text = str(context_chunk or "").strip()
                    chunk_key = chunk_text.lower()
                    if not chunk_text or chunk_key in seen_context:
                        continue
                    seen_context.add(chunk_key)
                    retrieved_context.append(chunk_text)
                    if len(retrieved_context) >= self._config.max_context_chunks:
                        break

                for item in retrieval.get("citations", []):
                    citation_doc_id = str(item.get("doc_id") or "")
                    citation_snippet = str(item.get("snippet") or "")
                    citation_key = (citation_doc_id, citation_snippet.strip().lower())
                    if citation_key in seen_citations:
                        continue
                    seen_citations.add(citation_key)
                    raw_citations.append(item)

        citations = [self._helpers.citation_factory(item) for item in raw_citations]
        citations = [
            citation
            for citation in citations
            if self._helpers.citation_matches_allowed_acts(citation, act_filters)
        ]
        citations.sort(key=lambda item: item.confidence, reverse=True)
        citations = citations[: self._config.max_citations]
        citations = self._helpers.normalize_citation_links(citations, request)

        explicit_act_aliases = self._extract_explicit_act_aliases(masked_query)

        if self._should_clarify_general_query(masked_query, citations, act_filters):
            return {
                "insufficient": True,
                "clarifying_question": (
                    "Please specify the Act (for example, BNS, IPC, BNSS, BSA, Constitution, IT Act) "
                    "and section/article if known so I can answer accurately."
                ),
                "requested_refs": requested_refs,
                "citations": [],
                "overall_confidence": None,
                "context_blocks": [],
            }

        if explicit_act_aliases:
            citations = [
                citation
                for citation in citations
                if self._citation_matches_act_aliases(citation, explicit_act_aliases)
            ]
            if not citations:
                return {
                    "insufficient": True,
                    "clarifying_question": (
                        "I could not find sources that match the requested Act. "
                        "Please confirm the Act name and section/article reference."
                    ),
                    "requested_refs": requested_refs,
                    "citations": [],
                    "overall_confidence": None,
                    "context_blocks": [],
                }

        if requested_refs and citations:
            if not any(
                self._helpers.citation_matches_requested_references(citation, requested_refs)
                for citation in citations
            ):
                requested_text = ", ".join(requested_refs)
                return {
                    "insufficient": True,
                    "clarifying_question": (
                        f"I could not find exact matches for the requested reference(s): {requested_text}. "
                        "Please confirm the Act and section/article reference."
                    ),
                    "requested_refs": requested_refs,
                    "citations": [],
                    "overall_confidence": None,
                    "context_blocks": [],
                }

            citations = [
                citation
                for citation in citations
                if self._helpers.citation_matches_requested_references(citation, requested_refs)
            ]

        context_blocks: list[str] = []
        for citation in citations:
            if citation.snippet:
                context_blocks.append(self._helpers.format_citation_context(citation))
            if len(context_blocks) >= self._config.max_context_chunks:
                break

        if not context_blocks and retrieved_context:
            context_blocks = retrieved_context[: self._config.max_context_chunks]

        if not context_blocks:
            return {
                "insufficient": True,
                "clarifying_question": (
                    "I could not find enough grounded legal sources for this query. "
                    "Please share the exact Act and section/article reference."
                ),
                "requested_refs": requested_refs,
                "citations": [],
                "overall_confidence": None,
                "context_blocks": [],
            }

        overall_confidence = None
        if citations:
            overall_confidence = round(
                sum(item.confidence for item in citations) / len(citations),
                2,
            )

        return {
            "insufficient": False,
            "clarifying_question": "",
            "requested_refs": requested_refs,
            "citations": citations,
            "overall_confidence": overall_confidence,
            "context_blocks": context_blocks,
        }

    def _judge(
        self,
        *,
        masked_query: str,
        requested_refs: list[str],
        context_blocks: list[str],
    ) -> JudgeDecision:
        context_text = "\n\n".join(context_blocks)
        context_text = context_text[: self._config.max_context_chars]
        refs_text = ", ".join(requested_refs) if requested_refs else "none"

        raw = self._judge_chain.invoke(
            {
                "query": masked_query,
                "requested_refs": refs_text,
                "context": context_text,
            }
        )
        return self._parse_judge_output(str(raw))

    def _normalize_answer(self, answer: str) -> str:
        normalized = self._llm_engine._enforce_subheading_bullets(answer)
        normalized = self._llm_engine._bold_legal_labels(normalized)
        return self._llm_engine._normalize_summary_table(normalized)

    def _should_skip_router_llm(
        self,
        act_filters: list[str | None],
        requested_refs: list[str],
    ) -> bool:
        if requested_refs:
            return True
        return any(item for item in act_filters if item)

    def _build_ref_expansions(self, requested_refs: list[str]) -> list[str]:
        expansions: list[str] = []
        for ref in requested_refs:
            if not ref:
                continue
            expansions.append(f"section {ref}")
        return self._dedupe_expansions(expansions)

    def _build_focus_query(self, masked_query: str) -> str:
        act_filters = self._helpers.infer_act_filters(masked_query)
        refs = self._helpers.extract_requested_references(masked_query)
        parts: list[str] = []
        for act in act_filters:
            if act:
                parts.append(act)
        parts.extend(refs)
        if not parts:
            return ""
        parts.append("meaning explanation punishment ingredients")
        return " ".join(parts)

    def _dedupe_expansions(self, expansions: list[str]) -> list[str]:
        results: list[str] = []
        seen: set[str] = set()
        for item in expansions:
            cleaned = " ".join(str(item or "").split())
            if not cleaned:
                continue
            if len(cleaned.split()) > 12:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(cleaned)
            if len(results) >= self._config.max_expansions:
                break
        return results

    def _should_clarify_general_query(
        self,
        masked_query: str,
        citations: list[Any],
        act_filters: list[str | None],
    ) -> bool:
        if not self._is_statute_intent(masked_query):
            return False

        required_aliases = self._infer_required_statute_aliases(masked_query)
        explicit_aliases = self._extract_explicit_act_aliases(masked_query)
        mentions_ref = self._query_mentions_reference(masked_query)

        if not explicit_aliases and not mentions_ref:
            return True

        target_aliases = explicit_aliases or required_aliases

        if target_aliases:
            return not any(
                self._citation_matches_act_aliases(citation, target_aliases)
                for citation in citations
            )

        return not any(self._looks_like_statute_citation(citation) for citation in citations)

    def _parse_router_output(
        self,
        raw_text: str,
        fallback: RouterDecision,
    ) -> tuple[RouterDecision, bool]:
        data = self._safe_parse_json(raw_text)
        if data is None:
            logger.debug("Router JSON invalid; using heuristic routing")
            return fallback, False

        act_filters = self._normalize_act_filters(data.get("act_filters"))
        expansions = self._dedupe_expansions(data.get("expansions") or [])
        needs_case_law = bool(data.get("needs_case_law"))
        needs_recent_case_law = bool(data.get("needs_recent_case_law"))

        if not act_filters:
            act_filters = fallback.act_filters

        decision = RouterDecision(
            act_filters=act_filters,
            expansions=expansions or fallback.expansions,
            needs_case_law=needs_case_law,
            needs_recent_case_law=needs_recent_case_law,
        )
        return decision, True

    def _parse_judge_output(self, raw_text: str) -> JudgeDecision:
        data = self._safe_parse_json(raw_text)
        if data is None:
            extracted = self._extract_non_json_fields(raw_text)
            if extracted.final_answer:
                return JudgeDecision(status="sufficient", final_answer=extracted.final_answer)
            if extracted.clarifying_question:
                return JudgeDecision(
                    status="insufficient",
                    clarifying_question=extracted.clarifying_question,
                )

            cleaned = raw_text.strip()
            if cleaned:
                return JudgeDecision(status="sufficient", final_answer=cleaned)
            return JudgeDecision(
                status="insufficient",
                clarifying_question=(
                    "Please specify the Act and section/article reference you want covered."
                ),
            )

        status = str(data.get("status") or "").strip().lower()
        if status not in {"sufficient", "insufficient"}:
            status = "insufficient"

        final_answer = data.get("final_answer")
        clarifying_question = data.get("clarifying_question")
        if status == "sufficient" and not final_answer:
            status = "insufficient"

        if status == "insufficient" and not clarifying_question:
            clarifying_question = (
                "Please specify the Act and section/article reference you want covered."
            )

        return JudgeDecision(
            status=status,
            final_answer=str(final_answer) if final_answer else None,
            clarifying_question=str(clarifying_question) if clarifying_question else None,
        )

    def _safe_parse_json(self, raw_text: str) -> dict[str, Any] | None:
        if not raw_text:
            return None

        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```\w*\n|```$", "", cleaned, flags=re.MULTILINE).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None

        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.debug("Failed to parse router/judge JSON")
            return None

    def _extract_non_json_fields(self, raw_text: str) -> JudgeDecision:
        status_match = re.search(r"\"status\"\s*:\s*\"(sufficient|insufficient)\"", raw_text, flags=re.IGNORECASE)
        status = status_match.group(1).lower() if status_match else ""

        final_answer = self._extract_inline_field(raw_text, "final_answer")
        clarifying_question = self._extract_inline_field(raw_text, "clarifying_question")

        if status == "sufficient" and final_answer:
            return JudgeDecision(status="sufficient", final_answer=final_answer)

        if status == "insufficient" and clarifying_question:
            return JudgeDecision(status="insufficient", clarifying_question=clarifying_question)

        if final_answer:
            return JudgeDecision(status="sufficient", final_answer=final_answer)

        if clarifying_question:
            return JudgeDecision(status="insufficient", clarifying_question=clarifying_question)

        return JudgeDecision(status="insufficient")

    @staticmethod
    def _extract_inline_field(raw_text: str, field_name: str) -> str | None:
        pattern = rf"\"{re.escape(field_name)}\"\s*:\s*\""
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if not match:
            return None

        tail = raw_text[match.end():]
        end_match = re.search(r"\"\s*[,}]\s*", tail)
        value = tail if end_match is None else tail[: end_match.start()]

        value = value.replace("\\n", "\n").replace("\\\"", "\"")
        return value.strip() or None

    def _normalize_act_filters(self, values: Any) -> list[str | None]:
        if not isinstance(values, list):
            return []

        normalized: list[str | None] = []
        for item in values:
            canonical = self._canonical_act_name(item)
            if canonical not in normalized:
                normalized.append(canonical)

        return normalized

    def _canonical_act_name(self, value: Any) -> str | None:
        token = self._normalize_token(str(value or ""))
        if not token or token in {"none", "null"}:
            return None

        mapping = {
            "bharatiyanyayasanhita": "Bharatiya Nyaya Sanhita",
            "bns": "Bharatiya Nyaya Sanhita",
            "bharatiyanagariksurakshasanhita": "Bharatiya Nagarik Suraksha Sanhita",
            "bnss": "Bharatiya Nagarik Suraksha Sanhita",
            "bharatiyasakshyaadhiniyam": "Bharatiya Sakshya Adhiniyam",
            "bsa": "Bharatiya Sakshya Adhiniyam",
            "constitutionofindia": "Constitution of India",
            "constitution": "Constitution of India",
            "informationtechnologyact2000": "Information Technology Act, 2000",
            "informationtechnologyact": "Information Technology Act, 2000",
            "itact": "Information Technology Act, 2000",
            "itact2000": "Information Technology Act, 2000",
            "indiancaselaw": "Indian Case Law",
            "caselaw": "Indian Case Law",
            "indian case law": "Indian Case Law",
        }

        return mapping.get(token)

    @staticmethod
    def _normalize_token(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    def _extract_explicit_act_aliases(self, query: str) -> set[str]:
        normalized = str(query or "").lower()
        aliases: set[str] = set()

        if re.search(r"\bbnss\b", normalized):
            aliases.add("bnss")
        if re.search(r"\bbns\b", normalized):
            aliases.add("bns")
        if re.search(r"\bbsa\b", normalized):
            aliases.add("bsa")
        if re.search(r"\bipc\b", normalized):
            aliases.add("ipc")
        if "constitution" in normalized or "article" in normalized:
            aliases.add("constitution")
        if "information technology" in normalized or "it act" in normalized:
            aliases.add("itact")

        return aliases

    def _infer_required_statute_aliases(self, query: str) -> set[str]:
        normalized = str(query or "").lower()
        if "information technology" in normalized or "it act" in normalized:
            return {"itact"}
        if "constitution" in normalized or "article" in normalized:
            return {"constitution"}
        if "evidence" in normalized or "sakshya" in normalized:
            return {"bsa"}
        if "bail" in normalized or "custody" in normalized or "remand" in normalized:
            return {"bnss"}

        crime_terms = (
            "rape",
            "sexual assault",
            "assault",
            "murder",
            "theft",
            "fraud",
            "harassment",
            "dowry",
            "kidnap",
            "abduct",
            "pocso",
        )
        if any(term in normalized for term in crime_terms):
            return {"bns", "ipc"}

        return set()

    def _citation_matches_act_aliases(self, citation: Any, aliases: set[str]) -> bool:
        if not aliases:
            return False
        raw_haystack = " ".join(
            [
                str(getattr(citation, "title", "") or ""),
                str(getattr(citation, "source", "") or ""),
                str(getattr(citation, "doc_id", "") or ""),
            ]
        )
        normalized = self._normalize_token(raw_haystack)
        token_haystack = self._tokenize_haystack(raw_haystack)

        expanded_aliases: set[str] = set()
        for alias in aliases:
            if alias == "itact":
                expanded_aliases.update({"itact", "informationtechnologyact", "informationtechnologyact2000"})
            elif alias == "ipc":
                expanded_aliases.update({"ipc", "indianpenalcode"})
            else:
                expanded_aliases.add(alias)

        for alias in expanded_aliases:
            if alias in {"bns", "bnss", "bsa", "ipc"}:
                if alias in token_haystack:
                    return True
                continue
            if alias in normalized:
                return True

        return False

    def _is_statute_intent(self, query: str) -> bool:
        normalized = str(query or "").lower()
        keywords = (
            "law",
            "laws",
            "act",
            "section",
            "article",
            "punishment",
            "penalty",
            "bns",
            "bnss",
            "bsa",
            "ipc",
            "constitution",
            "it act",
        )
        return any(word in normalized for word in keywords)

    @staticmethod
    def _query_mentions_reference(query: str) -> bool:
        return bool(re.search(r"\b(?:section|sec\.?|article|art\.?)\s*\d+", str(query or ""), flags=re.IGNORECASE))

    def _looks_like_statute_citation(self, citation: Any) -> bool:
        doc_type = str(getattr(citation, "doc_type", "") or "").lower()
        if doc_type == "statute":
            return True

        raw_haystack = " ".join(
            [
                str(getattr(citation, "title", "") or ""),
                str(getattr(citation, "source", "") or ""),
                str(getattr(citation, "doc_id", "") or ""),
            ]
        )
        normalized = self._normalize_token(raw_haystack)
        token_haystack = self._tokenize_haystack(raw_haystack)

        short_aliases = {"bns", "bnss", "bsa", "ipc"}
        if any(alias in token_haystack for alias in short_aliases):
            return True

        return any(alias in normalized for alias in ("constitution", "itact", "informationtechnologyact"))

    @staticmethod
    def _tokenize_haystack(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))
