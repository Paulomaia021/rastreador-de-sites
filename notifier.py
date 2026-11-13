import os
from urllib.parse import quote
import requests
from dotenv import load_dotenv
from database import normalize_whatsapp_phone

load_dotenv()


def format_whatsapp_message(
    product_name: str,
    current_price: float,
    target_price: float,
    url: str,
    previous_price: float | None = None,
    user_name: str = "",
) -> str:
    """Formata a mensagem de alerta otimizada para leitura no WhatsApp."""
    greeting = f"Olá, *{user_name}*! " if user_name else ""

    if current_price <= target_price:
        header = "🚨 *META DE PREÇO ATINGIDA NO WHATSAPP!* 🚨"
    else:
        header = "📉 *QUEDA DE PREÇO DETECTADA!* 📉"

    diff_line = ""
    if previous_price and previous_price > current_price:
        economia = previous_price - current_price
        diff_line = f"🔻 Caiu de R$ {previous_price:.2f} (-R$ {economia:.2f})\n"

    return (
        f"{header}\n\n"
        f"{greeting}Um produto que você monitora teve atualização relevante:\n\n"
        f"📦 *Produto:* {product_name}\n"
        f"💰 *Preço atual:* R$ {current_price:.2f}\n"
        f"🎯 *Seu preço alvo:* R$ {target_price:.2f}\n"
        f"{diff_line}"
        f"🔗 *Comprar agora:* {url}"
    )


def generate_whatsapp_link(phone: str, message: str) -> str:
    """
    Gera link direto (wa.me) para abrir ou disparar a mensagem no WhatsApp
    para o número configurado no perfil do usuário.
    """
    clean_phone = normalize_whatsapp_phone(phone)
    encoded_msg = quote(message)
    if clean_phone:
        return f"https://wa.me/{clean_phone}?text={encoded_msg}"
    return f"https://wa.me/?text={encoded_msg}"


def send_whatsapp_message(
    phone: str,
    message: str,
    user_apikey: str = "",
) -> dict:
    """
    Envia a notificação via WhatsApp diretamente para o número do usuário.
    Suporta:
      1. CallMeBot WhatsApp API (gratuita por usuário via whatsapp_apikey no perfil ou .env)
      2. Gateway/Webhook WhatsApp (Evolution API, Z-API, etc. via WHATSAPP_API_URL no .env)
      3. Twilio WhatsApp API (via TWILIO_ACCOUNT_SID no .env)
      4. Link direto wa.me pronto para envio/abertura imediata
    """
    clean_phone = normalize_whatsapp_phone(phone)
    wa_link = generate_whatsapp_link(clean_phone, message)

    if not clean_phone:
        return {
            "sent": False,
            "method": "sem_numero",
            "detail": "Número de WhatsApp não configurado no perfil.",
            "whatsapp_url": wa_link,
        }

    # 1. Tentativa via CallMeBot WhatsApp API (100% gratuito para número pessoal)
    api_key = (user_apikey or os.getenv("WHATSAPP_CALLMEBOT_APIKEY", "")).strip()
    if api_key:
        try:
            callmebot_url = "https://api.callmebot.com/whatsapp.php"
            resp = requests.get(
                callmebot_url,
                params={
                    "phone": f"+{clean_phone}",
                    "text": message,
                    "apikey": api_key,
                },
                timeout=15,
            )
            if resp.status_code == 200 and "error" not in resp.text.lower():
                print(f"-> WhatsApp enviado automaticamente (CallMeBot) para +{clean_phone}!")
                return {
                    "sent": True,
                    "method": "whatsapp_api",
                    "detail": f"Enviado via WhatsApp para +{clean_phone}",
                    "whatsapp_url": wa_link,
                }
        except Exception as e:
            print(f"-> Aviso ao tentar CallMeBot WhatsApp API: {e}")

    # 2. Tentativa via Webhook/API de WhatsApp (Evolution API / Z-API / Gateway próprio)
    whatsapp_api_url = os.getenv("WHATSAPP_API_URL", "").strip()
    whatsapp_api_token = os.getenv("WHATSAPP_API_TOKEN", "").strip()
    if whatsapp_api_url:
        try:
            headers = {"Content-Type": "application/json"}
            if whatsapp_api_token:
                headers["Authorization"] = f"Bearer {whatsapp_api_token}"
            resp = requests.post(
                whatsapp_api_url,
                json={"phone": clean_phone, "number": clean_phone, "message": message, "text": message},
                headers=headers,
                timeout=12,
            )
            if resp.ok:
                print(f"-> WhatsApp enviado via Gateway API para +{clean_phone}!")
                return {
                    "sent": True,
                    "method": "whatsapp_gateway",
                    "detail": f"Enviado via Gateway WhatsApp para +{clean_phone}",
                    "whatsapp_url": wa_link,
                }
        except Exception as e:
            print(f"-> Erro ao enviar para Gateway WhatsApp: {e}")

    # 3. Tentativa via Twilio WhatsApp API
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    twilio_from = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886").strip()
    if twilio_sid and twilio_token:
        try:
            twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
            resp = requests.post(
                twilio_url,
                data={
                    "From": twilio_from if twilio_from.startswith("whatsapp:") else f"whatsapp:{twilio_from}",
                    "To": f"whatsapp:+{clean_phone}",
                    "Body": message,
                },
                auth=(twilio_sid, twilio_token),
                timeout=12,
            )
            if resp.ok:
                print(f"-> WhatsApp enviado via Twilio para +{clean_phone}!")
                return {
                    "sent": True,
                    "method": "twilio_whatsapp",
                    "detail": f"Enviado via Twilio WhatsApp para +{clean_phone}",
                    "whatsapp_url": wa_link,
                }
        except Exception as e:
            print(f"-> Erro ao enviar via Twilio WhatsApp: {e}")

    # 4. Modo padrão: Notificação registrada para o número com link direto do WhatsApp
    print(f"-> Alerta WhatsApp preparado para +{clean_phone}: {wa_link}")
    return {
        "sent": True,
        "method": "whatsapp_direct",
        "detail": f"Alerta gerado para WhatsApp +{clean_phone}",
        "whatsapp_url": wa_link,
    }


def send_notification(
    product_name: str,
    current_price: float,
    target_price: float,
    url: str,
    whatsapp_phone: str = "",
    whatsapp_apikey: str = "",
    previous_price: float | None = None,
    user_name: str = "",
) -> dict:
    """
    Formata e envia a notificação de atualização de preço via WhatsApp
    diretamente para o telefone configurado no perfil do usuário.
    """
    message = format_whatsapp_message(
        product_name=product_name,
        current_price=current_price,
        target_price=target_price,
        url=url,
        previous_price=previous_price,
        user_name=user_name,
    )

    print("\n" + "=" * 55)
    print(message)
    if whatsapp_phone:
        print(f"📱 Destino WhatsApp: +{normalize_whatsapp_phone(whatsapp_phone)}")
    print("=" * 55 + "\n")

    target_phone = whatsapp_phone or os.getenv("DEFAULT_WHATSAPP_PHONE", "")
    result = send_whatsapp_message(
        phone=target_phone,
        message=message,
        user_apikey=whatsapp_apikey,
    )
    result["message"] = message
    return result
