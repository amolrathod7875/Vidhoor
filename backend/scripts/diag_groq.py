"""Diagnostic script for Groq API connectivity."""

from __future__ import annotations

import os
import time
import logging

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        logger.error("GROQ_API_KEY is not set.")
        return

    model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b").strip()

    try:
        from langchain_groq import ChatGroq
    except ImportError:
        logger.error("langchain-groq is not installed.")
        return

    llm = ChatGroq(
        api_key=api_key,
        model=model,
        temperature=0.0,
        max_retries=0,
    )

    prompt = "Say 'Groq diagnostic OK' and nothing else."
    start = time.perf_counter()
    try:
        response = llm.invoke(prompt)
        latency = time.perf_counter() - start
        content = getattr(response, "content", str(response))
        logger.info("provider=groq")
        logger.info("model=%s", model)
        logger.info("latency=%.2fs", latency)
        logger.info("success=true")
        logger.info("response=%s", content)
    except Exception as exc:
        latency = time.perf_counter() - start
        logger.info("provider=groq")
        logger.info("model=%s", model)
        logger.info("latency=%.2fs", latency)
        logger.info("success=false")
        logger.info("error=%s", exc)


if __name__ == "__main__":
    main()
