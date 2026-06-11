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

# python-telegram-bot's getUpdates long-poll emits one httpx INFO line per
# poll cycle, flooding the log. Keep third-party HTTP chatter at WARNING.
for _noisy in ("httpx", "httpcore", "telegram", "apscheduler"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


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

    # Start Telegram bot if configured
    telegram_app = None
    try:
        from monitoring.telegram import create_telegram_app
        telegram_app = create_telegram_app()
        if telegram_app is not None:
            await telegram_app.initialize()
            await telegram_app.start()
            await telegram_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot started (polling)")
    except Exception as e:
        logger.warning("Telegram bot failed to start: %s", e)

    # Keep running until interrupted
    try:
        while engine.running:
            await asyncio.sleep(5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if telegram_app is not None:
            try:
                await telegram_app.updater.stop()
                await telegram_app.stop()
                await telegram_app.shutdown()
            except Exception:
                pass
        if engine.running:
            await engine.stop()
        logger.info("Bot shut down.")


if __name__ == "__main__":
    if "--web" in sys.argv:
        import uvicorn
        from dotenv import load_dotenv

        load_dotenv()

        from config.settings import WEB_PORT
        from web.server import create_app

        app = create_app()
        port = WEB_PORT
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
