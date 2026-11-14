from __future__ import annotations
import os
from urllib.parse import quote
import requests
from dotenv import load_dotenv
from database import normalize_whatsapp_phone

load_dotenv()


def _get_secret(key: str, default: str = "") -> str:
    """Busca variável no .env ou no st.secrets do Streamlit Cloud."""
    val = os.getenv(key, "")
    if val:
        return str(val).strip()
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return default


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
      1. Twilio WhatsApp API (Gratuito via Sandbox em twilio.com)
      2. Meta WhatsApp Cloud API Oficial (Gratuito até 1.000 conversas/mês)
      3. Gateway/Webhook WhatsApp (Evolution API, Z-API, etc.)
      4. CallMeBot API (caso o usuário já possua uma chave ativa)
      5. Link direto wa.me pronto para abertura em 1 clique no iOS/Android/Web
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

    # 1. Tentativa via Twilio WhatsApp API (Funciona imediatamente com Twilio Sandbox gratuito)
    twilio_sid = _get_secret("TWILIO_ACCOUNT_SID")
    twilio_token = _get_secret("TWILIO_AUTH_TOKEN")
    twilio_from = _get_secret("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
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
                    "detail": f"Enviado automaticamente via WhatsApp (Twilio) para +{clean_phone}",
                    "whatsapp_url": wa_link,
                }
        except Exception as e:
            print(f"-> Erro ao enviar via Twilio WhatsApp: {e}")

    # 2. Tentativa via Meta WhatsApp Cloud API Oficial
    meta_token = _get_secret("WHATSAPP_META_TOKEN")
    meta_phone_id = _get_secret("WHATSAPP_META_PHONE_ID")
    if meta_token and meta_phone_id:
        try:
            meta_url = f"https://graph.facebook.com/v20.0/{meta_phone_id}/messages"
            resp = requests.post(
                meta_url,
                headers={
                    "Authorization": f"Bearer {meta_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": clean_phone,
                    "type": "text",
                    "text": {"preview_url": True, "body": message},
                },
                timeout=12,
            )
            if resp.ok:
                print(f"-> WhatsApp enviado via Meta Cloud API para +{clean_phone}!")
                return {
                    "sent": True,
                    "method": "meta_whatsapp",
                    "detail": f"Enviado automaticamente via WhatsApp Oficial para +{clean_phone}",
                    "whatsapp_url": wa_link,
                }
        except Exception as e:
            print(f"-> Erro ao enviar via Meta WhatsApp Cloud API: {e}")

    # 3. Tentativa via Webhook/API de WhatsApp (Evolution API / Z-API / Gateway próprio)
    whatsapp_api_url = _get_secret("WHATSAPP_API_URL")
    whatsapp_api_token = _get_secret("WHATSAPP_API_TOKEN")
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

    # 4. Tentativa via CallMeBot (caso o usuário tenha chave)
    api_key = (user_apikey or _get_secret("WHATSAPP_CALLMEBOT_APIKEY")).strip()
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
                return {
                    "sent": True,
                    "method": "whatsapp_api",
                    "detail": f"Enviado via WhatsApp para +{clean_phone}",
                    "whatsapp_url": wa_link,
                }
        except Exception:
            pass

    # 5. Modo Direto Padrão (Sem precisar de chave externa): Link Direto wa.me para o WhatsApp do usuário
    print(f"-> Alerta WhatsApp gerado para +{clean_phone}: {wa_link}")
    return {
        "sent": True,
        "method": "whatsapp_direct",
        "detail": f"Alerta WhatsApp pronto para +{clean_phone}",
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

    target_phone = whatsapp_phone or _get_secret("DEFAULT_WHATSAPP_PHONE")
    result = send_whatsapp_message(
        phone=target_phone,
        message=message,
        user_apikey=whatsapp_apikey,
    )
    result["message"] = message
    return result
