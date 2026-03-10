"""Polymarket Signal-Based Trading Bot — Entry Point."""

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Main orchestrator loop. Implemented in a later section."""
    logger.info("Polymarket trading bot starting...")
    # Full implementation in Section 8+
    raise NotImplementedError("Main loop not yet implemented — build sections 1-8 first.")


if __name__ == "__main__":
    if "--tui" in sys.argv:
        from dotenv import load_dotenv

        load_dotenv()

        from tui.app import TUIApp

        app = TUIApp()
        app.run()
    else:
        asyncio.run(main())
