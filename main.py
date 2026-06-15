# ============================================================
# File: main.py
# Role: Точка входа (запуск бота и настройка окружения)
# ============================================================

# File: main.py
# Role: Entry point for the arbitrage bot. Orchestrates initialization and startup.

import asyncio
import sys
from dotenv import load_dotenv

load_dotenv()

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from CORE.bot import ASB
from c_log import UnifiedLogger

def main() -> None:
    logger = UnifiedLogger(name="core", context="MAIN")
    try:
        bot = ASB(logger=logger)
        asyncio.run(bot.run_forever())
    except KeyboardInterrupt:
        logger.info("[APP] stopped by user")
    except Exception as e:
        logger.error(f"[APP] startup/runtime fatal: {e}")
        raise


if __name__ == "__main__":
    main()

## шпору не трогать!!
# # chmod 600 ssh_key.txt
# # eval "$(ssh-agent -s)" 
# # ssh-add ssh_key.txt
# # git remote set-url origin git@github.com:hotelUpz/uranus_bot.git
# # source .ssh-autostart.sh
# # git push --set-upstream origin master
# # git config --global push.autoSetupRemote true
# # ssh -T git@github.com 
# # git log -1

# # git add .
# # git commit -m "plh37"
# # git push

# # pip install anthropic
# # npm install -g @anthropic-ai/claude-code

# # export ANTHROPIC_API_KEY=...
# taskkill /F /IM python.exe

# # claude