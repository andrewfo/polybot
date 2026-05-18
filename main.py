"""Polymarket Signal-Based Trading Bot — Entry Point."""

import asyncio
import logging
import os
import sys

os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/bot.log"),
    ],
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Main orchestrator loop — runs the bot without the web dashboard."""
    from dotenv import load_dotenv

    load_dotenv()

    logger.info("Polymarket trading bot starting (headless mode)...")

    from web.server import BotEngine, WSManager

    # Dummy WS manager (no WebSocket connections in headless mode)
    ws = WSManager()
    engine = BotEngine(ws)
    await engine.start()

    # Keep running until interrupted
    try:
        while engine.running:
            await asyncio.sleep(5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if engine.running:
            await engine.stop()
        logger.info("Bot shut down.")


if __name__ == "__main__":
    if "--web" in sys.argv:
        import uvicorn
        from dotenv import load_dotenv

        load_dotenv()

        from web.server import create_app

        app = create_app()
        port = int(os.environ.get("WEB_PORT", "8080"))
        reload = "--reload" in sys.argv
        uvicorn.run(
            "web.server:create_app",
            host="127.0.0.1",
            port=port,
            reload=reload,
            factory=True,
        )
    else:
        asyncio.run(main())
