from __future__ import annotations
import os
from urllib.parse import quote
import requests
from dotenv import load_dotenv
from database import (
    normalize_whatsapp_phone,
    is_valid_whatsapp_phone,
    record_system_log,
    require_admin,
    get_connection,
    admin_update_notification_status,
    record_audit_log,
)

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


def get_integrations_status() -> list[dict]:
    """Retorna o status atual de todas as integrações de notificação e scraping."""
    twilio_ok = bool(_get_secret("TWILIO_ACCOUNT_SID") and _get_secret("TWILIO_AUTH_TOKEN"))
    meta_ok = bool(_get_secret("WHATSAPP_META_TOKEN") and _get_secret("WHATSAPP_META_PHONE_ID"))
    gateway_ok = bool(_get_secret("WHATSAPP_API_URL"))
    callmebot_ok = bool(_get_secret("WHATSAPP_CALLMEBOT_APIKEY"))

    return [
        {
            "provider": "WhatsApp Direct (wa.me)",
            "status": "🟢 Ativo",
            "type": "Link Direto (iOS / Android / Web)",
            "details": "Pronto para uso imediato sem necessidade de token externo.",
        },
        {
            "provider": "Meta WhatsApp Cloud API (Oficial)",
            "status": "🟢 Configurado" if meta_ok else "⚪ Não configurado",
            "type": "API Oficial Meta",
            "details": "Variáveis WHATSAPP_META_TOKEN e WHATSAPP_META_PHONE_ID.",
        },
        {
            "provider": "Twilio WhatsApp API",
            "status": "🟢 Configurado" if twilio_ok else "⚪ Não configurado",
            "type": "API Oficial / Sandbox",
            "details": "Variáveis TWILIO_ACCOUNT_SID e TWILIO_AUTH_TOKEN.",
        },
        {
            "provider": "Gateway / Webhook WhatsApp",
            "status": "🟢 Configurado" if gateway_ok else "⚪ Não configurado",
            "type": "Evolution API / Z-API / Webhook",
            "details": "Variável WHATSAPP_API_URL.",
        },
        {
            "provider": "CallMeBot WhatsApp API",
            "status": "🟢 Configurado" if callmebot_ok else "⚪ Opcional por perfil",
            "type": "Bot Pessoal",
            "details": "Configurável individualmente no perfil do usuário.",
        },
    ]


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
    user_id: int | None = None,
    product_id: int | None = None,
) -> dict:
    """
    Envia a notificação via WhatsApp diretamente para o número do usuário.
    Utiliza os status padronizados: 'Pendente', 'Enviada', 'Entregue', 'Falha'.
    """
    clean_phone = normalize_whatsapp_phone(phone)
    wa_link = generate_whatsapp_link(clean_phone, message)

    if not clean_phone or not is_valid_whatsapp_phone(clean_phone):
        err_msg = f"Número de WhatsApp ausente ou inválido: '{phone}'"
        record_system_log(
            event_type="whatsapp",
            status="erro",
            message=err_msg,
            user_id=user_id,
            product_id=product_id,
        )
        return {
            "sent": False,
            "delivery_status": "Falha",
            "method": "numero_invalido",
            "detail": err_msg,
            "error_message": err_msg,
            "whatsapp_url": wa_link,
        }

    api_errors: list[str] = []

    # 1. Tentativa via Twilio WhatsApp API
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
                record_system_log(
                    event_type="whatsapp",
                    status="sucesso",
                    message=f"Mensagem entregue ao provedor Twilio para +{clean_phone}",
                    user_id=user_id,
                    product_id=product_id,
                    http_status=resp.status_code,
                )
                return {
                    "sent": True,
                    "delivery_status": "Entregue",
                    "method": "twilio_whatsapp",
                    "detail": f"Entregue via Twilio WhatsApp para +{clean_phone}",
                    "error_message": "",
                    "whatsapp_url": wa_link,
                }
            api_errors.append(f"Twilio HTTP {resp.status_code}: {resp.text[:180]}")
        except Exception as e:
            api_errors.append(f"Twilio erro: {e}")

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
                record_system_log(
                    event_type="whatsapp",
                    status="sucesso",
                    message=f"Mensagem confirmada pela Meta Cloud API para +{clean_phone}",
                    user_id=user_id,
                    product_id=product_id,
                    http_status=resp.status_code,
                )
                return {
                    "sent": True,
                    "delivery_status": "Entregue",
                    "method": "meta_whatsapp",
                    "detail": f"Entregue via WhatsApp Oficial (Meta) para +{clean_phone}",
                    "error_message": "",
                    "whatsapp_url": wa_link,
                }
            api_errors.append(f"Meta API HTTP {resp.status_code}: {resp.text[:180]}")
        except Exception as e:
            api_errors.append(f"Meta API erro: {e}")

    # 3. Tentativa via Webhook/API de WhatsApp (Evolution API / Z-API)
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
                record_system_log(
                    event_type="whatsapp",
                    status="sucesso",
                    message=f"Mensagem entregue via Gateway para +{clean_phone}",
                    user_id=user_id,
                    product_id=product_id,
                    http_status=resp.status_code,
                )
                return {
                    "sent": True,
                    "delivery_status": "Entregue",
                    "method": "whatsapp_gateway",
                    "detail": f"Entregue via Gateway WhatsApp para +{clean_phone}",
                    "error_message": "",
                    "whatsapp_url": wa_link,
                }
            api_errors.append(f"Gateway HTTP {resp.status_code}: {resp.text[:180]}")
        except Exception as e:
            api_errors.append(f"Gateway erro: {e}")

    # 4. Tentativa via CallMeBot (se o usuário configurou chave)
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
                record_system_log(
                    event_type="whatsapp",
                    status="sucesso",
                    message=f"Mensagem entregue via CallMeBot para +{clean_phone}",
                    user_id=user_id,
                    product_id=product_id,
                    http_status=resp.status_code,
                )
                return {
                    "sent": True,
                    "delivery_status": "Entregue",
                    "method": "callmebot_api",
                    "detail": f"Entregue via CallMeBot para +{clean_phone}",
                    "error_message": "",
                    "whatsapp_url": wa_link,
                }
            api_errors.append(f"CallMeBot HTTP {resp.status_code}: {resp.text[:180]}")
        except Exception as e:
            api_errors.append(f"CallMeBot erro: {e}")

    # Se alguma API externa foi tentada e falhou, registra a falha detalhada
    if api_errors:
        joined_err = " | ".join(api_errors)
        record_system_log(
            event_type="whatsapp",
            status="erro",
            message=f"Falha na API de WhatsApp para +{clean_phone}: {joined_err}",
            user_id=user_id,
            product_id=product_id,
        )
        return {
            "sent": False,
            "delivery_status": "Falha",
            "method": "api_error",
            "detail": f"Falha no provedor de API para +{clean_phone}",
            "error_message": joined_err,
            "whatsapp_url": wa_link,
        }

    # 5. Modo Direto Padrão (Link Direto wa.me)
    record_system_log(
        event_type="whatsapp",
        status="sucesso",
        message=f"Notificação WhatsApp gerada (link direto) para +{clean_phone}",
        user_id=user_id,
        product_id=product_id,
    )
    return {
        "sent": True,
        "delivery_status": "Enviada",
        "method": "whatsapp_direct",
        "detail": f"Enviada (Link Direto WhatsApp) para +{clean_phone}",
        "error_message": "",
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
    user_id: int | None = None,
    product_id: int | None = None,
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
        user_id=user_id,
        product_id=product_id,
    )
    result["message"] = message
    return result


def send_recovery_code_whatsapp(rec_info: dict) -> dict:
    """
    Envia o código de recuperação de 6 dígitos diretamente pelo backend via WhatsApp,
    garantindo que o código NUNCA seja exposto em URLs no navegador (wa.me/?text=...).
    """
    display_name = rec_info.get("display_name") or rec_info.get("username") or "Usuário"
    phone = rec_info.get("whatsapp_phone", "")
    code = rec_info.get("code", "")
    user_apikey = rec_info.get("whatsapp_apikey", "")
    user_id = rec_info.get("user_id")

    recovery_msg = (
        f"🔐 *Recuperação de Conta — Rastreador de Preços*\n\n"
        f"Olá, *{display_name}*!\n"
        f"Seu código de segurança para redefinir sua senha é:\n\n"
        f"👉 *{code}*\n\n"
        f"Este código expira em 15 minutos. Se você não solicitou, ignore esta mensagem."
    )

    res = send_whatsapp_message(
        phone=phone,
        message=recovery_msg,
        user_apikey=user_apikey,
        user_id=user_id,
    )
    # Remove qualquer URL externa que possa conter o código de segurança na query string
    res["whatsapp_url"] = ""
    return res


def admin_retry_notification(admin_user_id: int, notification_id: int) -> dict:
    """
    Permite ao administrador reenviar uma notificação com falha ou pendente,
    atualizando seu status e contador de tentativas no banco de dados.
    """
    require_admin(admin_user_id)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT n.*, u.whatsapp_phone as user_phone, u.whatsapp_apikey
            FROM notification_logs n
            LEFT JOIN users u ON u.id = n.user_id
            WHERE n.id = ?
            """,
            (notification_id,),
        ).fetchone()
        if not row:
            raise ValueError("Notificação não encontrada.")
        notif = dict(row)

    phone_to_use = notif.get("user_phone") or notif.get("phone") or ""
    res = send_whatsapp_message(
        phone=phone_to_use,
        message=notif["message"],
        user_apikey=notif.get("whatsapp_apikey") or "",
        user_id=notif.get("user_id"),
        product_id=notif.get("product_id"),
    )

    admin_update_notification_status(
        admin_user_id=admin_user_id,
        notification_id=notification_id,
        delivery_status=res["delivery_status"],
        error_message=res.get("error_message", ""),
        increment_retry=True,
    )
    record_audit_log(
        admin_user_id=admin_user_id,
        action="REENVIAR_NOTIFICACAO",
        details=f"Reenvio da notificação ID {notification_id} para '{phone_to_use}' -> Status: {res['delivery_status']}",
        target_user_id=notif.get("user_id"),
    )
    return res
