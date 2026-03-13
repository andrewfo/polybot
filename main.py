"""Polymarket Signal-Based Trading Bot — Entry Point."""

import asyncio
import logging
import os
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
    if "--web" in sys.argv:
        import uvicorn
        from dotenv import load_dotenv

        load_dotenv()

        from web.server import create_app

        app = create_app()
        port = int(os.environ.get("WEB_PORT", "8080"))
        uvicorn.run(app, host="127.0.0.1", port=port)
    else:
        asyncio.run(main())
