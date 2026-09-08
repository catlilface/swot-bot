"""Entry point for the transcriber service."""

import asyncio


async def _amain() -> None:
    # Placeholder entry point; faster-whisper wiring lands in Этап 4 (task #11).
    print("swot-transcriber service — placeholder")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
