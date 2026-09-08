"""Entry point for the analyzer service."""

import asyncio


async def _amain() -> None:
    # Placeholder entry point; LLM+Langfuse wiring lands in Этап 5 (task #12).
    print("swot-analyzer service — placeholder")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
