import os
import requests
from dotenv import load_dotenv

load_dotenv()


def send_notification(product_name: str, current_price: float, target_price: float, url: str) -> None:
    """
    Envia alerta no console e, se configurado no arquivo .env,
    também dispara para Discord (Webhook) ou Telegram (Bot).
    """
    message = (
        f"🚨 *ALERTA DE PREÇO BAIXO!* 🚨\n"
        f"📦 Produto: {product_name}\n"
        f"💰 Preço atual: R$ {current_price:.2f} (Alvo: R$ {target_price:.2f})\n"
        f"🔗 Link: {url}"
    )

    # Sempre exibe no terminal
    print("\n" + "=" * 50)
    print(message)
    print("=" * 50 + "\n")

    # Opcional 1: Webhook do Discord
    discord_webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if discord_webhook:
        try:
            requests.post(discord_webhook, json={"content": message}, timeout=10)
            print("-> Notificação enviada para o Discord!")
        except Exception as e:
            print(f"-> Erro ao enviar para o Discord: {e}")

    # Opcional 2: Bot do Telegram
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if telegram_token and telegram_chat_id:
        try:
            tg_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
            requests.post(
                tg_url,
                json={"chat_id": telegram_chat_id, "text": message},
                timeout=10,
            )
            print("-> Notificação enviada para o Telegram!")
        except Exception as e:
            print(f"-> Erro ao enviar para o Telegram: {e}")
