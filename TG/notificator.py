# ============================================================
# File: TG/notificator.py
# Role: Отправка сообщений в Telegram через бота с контролем частоты запросов
# python -m TG.notificator
# ============================================================

import aiohttp
import asyncio
import time
import os
from c_log import UnifiedLogger

logger = UnifiedLogger("tg")

class TelegramSender:
    def __init__(self, token: str, chat_id: str, min_send_interval_sec: float = 1.0):
        self.token = token
        self.chat_id = str(chat_id)
        self.min_send_interval = min_send_interval_sec
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self._session: aiohttp.ClientSession | None = None
        
        # Инструменты для контроля лимитов
        self._lock = asyncio.Lock()
        self._last_send_time = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send_message(self, text: str):
        if not self.token or not self.chat_id: 
            return

        async with self._lock:
            # 1. Считаем, сколько времени прошло с последней отправки
            elapsed = time.monotonic() - self._last_send_time
            
            # 2. Если прошло меньше минимального интервала, "спим" оставшееся время
            if elapsed < self.min_send_interval:
                await asyncio.sleep(self.min_send_interval - elapsed)

            try:
                for attempt in range(2):
                    session = await self._get_session()
                    url = f"{self.api_url}/sendMessage"
                    payload = {
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "HTML"
                    }
                    
                    async with session.post(url, json=payload, timeout=10) as resp:
                        if resp.status == 429:
                            try:
                                err_data = await resp.json()
                                wait_sec = err_data.get("parameters", {}).get("retry_after", 5)
                            except Exception:
                                wait_sec = 5
                            logger.warning(f"TG Rate Limit [429]. Waiting {wait_sec}s before retry...")
                            await asyncio.sleep(wait_sec)
                            continue
                        elif resp.status != 200:
                            err_txt = await resp.text()
                            logger.error(f"TG API Error [{resp.status}]: {err_txt}")
                        break


            except Exception as e:
                logger.error(f"TG Send Error: {e}")
            finally:
                self._last_send_time = time.monotonic()

    async def aclose(self):
        if self._session and not self._session.closed:
            await self._session.close()

if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    from TG.messages import build_funding_signal_message
    import sys
    
    # Исправляем кодировку консоли
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
        
    load_dotenv()
    
    async def test_templates():
        token = os.getenv("TG_BOT_TOKEN")
        chat_id = os.getenv("TG_CHAT_ID")
        if not token or not chat_id:
            print("No TG credentials in .env")
            return
            
        print("Starting TG Sender Test...")
        sender = TelegramSender(token, chat_id)
        
        # Читаем шаблоны
        app_json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "CONFIG", "app.json")
        try:
            with open(app_json_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            templates = config.get("telegram", {}).get("templates", {})
        except Exception as e:
            print(f"Failed to load templates: {e}")
            templates = {}
            
        symbol = "SOL_USDT"
        
        for t_id in ["1"]:
            tpl_str = templates.get(t_id, "")
            if not tpl_str:
                continue
                
            msg_body = build_funding_signal_message(
                template_str=tpl_str,
                symbol=symbol,
                kind="ACTION",
                side="LONG",
                dom="binance",
                slv="phemex",
                price_spread=1.05,
                funding_spread=-0.0100,
                total=1.05,
                fund_d=0.0100,
                fund_s=0.0200,
                ttf_d=3600 * 4 + 1800,
                ttf_s=3600 * 4 + 1800,
                interval_d="8",
                interval_s="?"
            )
            
            final_msg = f"<b>--- ШАБЛОН {t_id} ---</b>\n\n{msg_body}"
            print(f"Sending Template {t_id}...")
            await sender.send_message(final_msg)
            
        await sender.aclose()
        print("Done!")

    asyncio.run(test_templates())