from __future__ import annotations
import importlib
import streamlit as st
import pandas as pd
import database
import scraper
import notifier
import main

# Garante que o Streamlit Cloud atualize os módulos base em memória ANTES de importar admin_panel
if not hasattr(database, "admin_get_dashboard_kpis") or not hasattr(database, "create_user_session"):
    importlib.reload(database)
if not hasattr(scraper, "ScraperError"):
    importlib.reload(scraper)
if not hasattr(notifier, "admin_retry_notification"):
    importlib.reload(notifier)
if not hasattr(main, "trigger_background_queue_worker"):
    importlib.reload(main)

import admin_panel
if not hasattr(admin_panel, "render_admin_panel"):
    importlib.reload(admin_panel)

from database import (
    init_db,
    create_user,
    authenticate_user,
    get_user_by_id,
    is_user_admin,
    update_user_profile,
    add_product,
    get_user_products,
    update_product_target,
    delete_product,
    get_user_price_history,
    get_user_notifications,
    create_user_session,
    get_user_from_session_token,
    delete_user_session,
    MIN_PASSWORD_LENGTH,
)
from main import check_all_products
from notifier import (
    format_whatsapp_message,
    generate_whatsapp_link,
)
from admin_panel import render_admin_panel

# 1. Configuração inicial da página
st.set_page_config(
    page_title="Rastreador de Preços | Multiusuário",
    page_icon="🏷️",
    layout="wide",
)

# Inicializa e migra o banco SQLite automaticamente
init_db()

# Controle de sessão do usuário autenticado
if "user" not in st.session_state:
    st.session_state["user"] = None
if "confirm_delete_id" not in st.session_state:
    st.session_state["confirm_delete_id"] = None
if "last_alerts" not in st.session_state:
    st.session_state["last_alerts"] = []

# Persistência e controle de sessão: expira após 15 minutos de inatividade
session_token = st.query_params.get("session_token")
if session_token:
    persisted_user = get_user_from_session_token(session_token, renew_minutes=15)
    if persisted_user:
        st.session_state["user"] = persisted_user
    else:
        # Sessão expirou (mais de 15 minutos inativo) -> realiza logout automático
        st.query_params.pop("session_token", None)
        st.session_state["user"] = None
        st.info("⏱️ Sua sessão expirou após 15 minutos de inatividade. Por favor, entre novamente.")


def render_auth_screen() -> None:
    """Exibe a tela de login e criação de conta individual."""
    st.title("🏷️ Rastreador Pessoal de Preços com WhatsApp")
    st.markdown(
        "Monitore seus produtos favoritos em um perfil **100% isolado e privado** e "
        "receba alertas de promoção diretamente no seu **WhatsApp**."
    )

    col_left, col_center, col_right = st.columns([1, 2, 1])
    with col_center:
        tab_login, tab_register = st.tabs(
            ["🔑 Entrar na Conta", "📝 Criar Meu Perfil"]
        )

        with tab_login:
            if st.session_state.get("pwd_reset_success_msg"):
                st.success(st.session_state.pop("pwd_reset_success_msg"))

            if not st.session_state.get("show_forgot_password", False):
                with st.form("login_form"):
                    st.subheader("Acessar meu painel")
                    username = st.text_input("Usuário", placeholder="Ex: paulo")
                    password = st.text_input("Senha", type="password")
                    submit_login = st.form_submit_button("Entrar", use_container_width=True)

                    if submit_login:
                        try:
                            with st.spinner("Autenticando suas credenciais..."):
                                user = authenticate_user(username, password)
                            if user:
                                token = create_user_session(user["id"])
                                st.query_params["session_token"] = token
                                st.session_state["user"] = user
                                st.session_state["last_alerts"] = []
                                st.rerun()
                            else:
                                st.error("Usuário ou senha incorretos. Verifique seus dados ou crie um perfil.")
                        except PermissionError as err:
                            st.error(str(err))

                if st.button("Esqueceu a senha?", use_container_width=True):
                    st.session_state["show_forgot_password"] = True
                    st.rerun()

            else:
                st.subheader("🔄 Recuperar Minha Senha")
                st.caption(
                    "Informe seu nome de usuário e confirme o número de WhatsApp cadastrado na conta "
                    "para receber o código de verificação de 6 dígitos (válido por 15 minutos)."
                )

                if "recovery_data" not in st.session_state:
                    st.session_state["recovery_data"] = None

                # Passo 1: Solicitar código
                with st.form("request_code_form"):
                    rec_identifier = st.text_input(
                        "Seu Nome de Usuário",
                        placeholder="Ex: paulo",
                    )
                    rec_confirm_phone = st.text_input(
                        "Confirme seu WhatsApp cadastrado (com DDD ou últimos 4 dígitos)",
                        placeholder="Ex: 21999998888 ou 8888",
                    )
                    btn_send_code = st.form_submit_button("📲 Solicitar Código de Recuperação", use_container_width=True)

                    if btn_send_code:
                        if not rec_identifier.strip() or not rec_confirm_phone.strip():
                            st.warning("Informe seu nome de usuário e confirme o WhatsApp cadastrado.")
                        else:
                            from database import create_recovery_token
                            from notifier import send_recovery_code_whatsapp
                            try:
                                with st.spinner("Validando conta e enviando código de verificação..."):
                                    rec_info = create_recovery_token(
                                        rec_identifier.strip(),
                                        confirm_phone=rec_confirm_phone.strip(),
                                    )
                                    dispatch_res = send_recovery_code_whatsapp(rec_info)
                                rec_info["dispatch_method"] = dispatch_res.get("method", "")
                                rec_info["dispatch_status"] = dispatch_res.get("delivery_status", "")
                                st.session_state["recovery_data"] = rec_info
                                st.rerun()
                            except Exception as err:
                                st.error(str(err))

                # Passo 2: Validar código e definir nova senha
                if st.session_state.get("recovery_data"):
                    rec_info = st.session_state["recovery_data"]
                    masked_phone = rec_info.get("masked_phone", "***")
                    dispatch_method = rec_info.get("dispatch_method", "")

                    st.markdown("---")
                    if dispatch_method in ("twilio_whatsapp", "meta_whatsapp", "whatsapp_gateway", "callmebot_api"):
                        st.success(
                            f"📲 Código de 6 dígitos enviado diretamente para o seu WhatsApp (**{masked_phone}**)."
                        )
                    else:
                        st.info(
                            f"📱 Identidade confirmada para **{masked_phone}**. "
                            f"Use o código temporário de verificação (válido por 15 min): **`{rec_info['code']}`**"
                        )

                    with st.form("submit_new_password_form"):
                        st.markdown("#### Passo 2: Digite o código de 6 dígitos e sua nova senha")
                        code_input = st.text_input("Código de 6 dígitos", placeholder="Ex: 123456", max_chars=6)
                        new_pwd1 = st.text_input(
                            f"Nova Senha (mínimo {MIN_PASSWORD_LENGTH} caracteres)",
                            type="password",
                            placeholder=f"Mínimo {MIN_PASSWORD_LENGTH} caracteres",
                        )
                        new_pwd2 = st.text_input(
                            "Confirme a Nova Senha",
                            type="password",
                            placeholder="Repita a nova senha",
                        )
                        btn_confirm_reset = st.form_submit_button("✅ Redefinir Senha", type="primary", use_container_width=True)

                        if btn_confirm_reset:
                            if not code_input.strip():
                                st.warning("Informe o código de verificação de 6 dígitos.")
                            elif len(new_pwd1.strip()) < MIN_PASSWORD_LENGTH:
                                st.error(f"A nova senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
                            elif new_pwd1 != new_pwd2:
                                st.error("As senhas digitadas não coincidem. Tente novamente.")
                            else:
                                from database import reset_password_with_token
                                try:
                                    with st.spinner("Validando código e atualizando sua senha..."):
                                        reset_password_with_token(
                                            user_id=rec_info["user_id"],
                                            code=code_input,
                                            new_password=new_pwd1,
                                        )
                                    st.session_state["recovery_data"] = None
                                    st.session_state["show_forgot_password"] = False
                                    st.session_state["pwd_reset_success_msg"] = (
                                        "🎉 Senha alterada com sucesso! Você já pode entrar com sua nova senha."
                                    )
                                    st.rerun()
                                except Exception as err:
                                    st.error(str(err))

                st.write("")
                if st.button("⬅️ Voltar para o Login", use_container_width=True):
                    st.session_state["show_forgot_password"] = False
                    st.session_state["recovery_data"] = None
                    st.rerun()

        with tab_register:
            with st.form("register_form"):
                st.subheader("Criar novo perfil individual")
                new_display_name = st.text_input("Seu Nome", placeholder="Ex: Paulo Maia")
                new_username = st.text_input("Nome de Usuário (para login)", placeholder="Ex: paulo")
                new_password = st.text_input(
                    f"Crie uma Senha (mínimo {MIN_PASSWORD_LENGTH} caracteres)",
                    type="password",
                    placeholder=f"Mínimo {MIN_PASSWORD_LENGTH} caracteres",
                )
                new_password_confirm = st.text_input(
                    "Confirme sua Senha",
                    type="password",
                    placeholder="Digite a mesma senha novamente",
                )
                new_whatsapp = st.text_input(
                    "Seu WhatsApp (com DDD)",
                    placeholder="Ex: 21999998888",
                    help="Digite apenas o DDD + número. Você receberá seus alertas de preço neste WhatsApp.",
                )
                submit_register = st.form_submit_button("Criar Conta e Entrar", use_container_width=True)

                if submit_register:
                    if not new_username.strip() or not new_password.strip():
                        st.warning("Preencha pelo menos o nome de usuário e a senha.")
                    elif len(new_password.strip()) < MIN_PASSWORD_LENGTH:
                        st.error(f"A senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
                    elif new_password != new_password_confirm:
                        st.error("As senhas informadas não coincidem. Digite a mesma senha nos dois campos.")
                    else:
                        try:
                            with st.spinner("Criando seu perfil seguro..."):
                                created = create_user(
                                    username=new_username,
                                    password=new_password,
                                    display_name=new_display_name,
                                    whatsapp_phone=new_whatsapp,
                                )
                                token = create_user_session(created["id"])
                            st.query_params["session_token"] = token
                            st.session_state["user"] = created
                            st.success("Perfil criado com sucesso!")
                            st.rerun()
                        except ValueError as err:
                            st.error(str(err))


# Se não estiver logado, mostra apenas a tela de autenticação
if not st.session_state["user"]:
    render_auth_screen()
    st.stop()

# Usuário autenticado: recarrega dados frescos do banco garantindo isolamento e checando se está ativo
current_user = get_user_by_id(st.session_state["user"]["id"])
if not current_user or current_user.get("is_active", 1) == 0:
    token_to_clear = st.query_params.get("session_token")
    if token_to_clear:
        delete_user_session(token_to_clear)
    st.query_params.pop("session_token", None)
    st.session_state["user"] = None
    st.rerun()
st.session_state["user"] = current_user
user_id = int(current_user["id"])
user_is_admin = is_user_admin(user_id)

# =====================================================================
# BARRA LATERAL: PERFIL, CADASTRO DE PRODUTO E LOGOUT
# =====================================================================
with st.sidebar:
    role_badge = "👑 Administrador" if user_is_admin else "👤 Conta Pessoal"
    st.markdown(f"### Olá, {current_user['display_name']}!")
    st.caption(f"Nível de acesso: **{role_badge}**")
    phone_display = (
        f"+{current_user['whatsapp_phone']}"
        if current_user.get("whatsapp_phone")
        else "⚠️ Não configurado"
    )
    st.caption(f"📱 WhatsApp ativo: **{phone_display}**")

    if st.button("🚪 Sair da Conta", use_container_width=True):
        current_token = st.query_params.get("session_token")
        if current_token:
            delete_user_session(current_token)
        st.query_params.pop("session_token", None)
        st.session_state["user"] = None
        st.session_state["confirm_delete_id"] = None
        st.session_state["last_alerts"] = []
        st.rerun()

    st.divider()

    st.header("➕ Adicionar Produto")
    with st.form("form_novo_produto", clear_on_submit=True):
        nome = st.text_input("Nome do Produto", placeholder="Ex: Fone Bluetooth Sony")
        url = st.text_input("Link (URL) do Produto", placeholder="https://...")
        alvo = st.number_input(
            "Preço Alvo Desejado (R$)",
            min_value=0.1,
            step=5.0,
            format="%.2f",
        )
        submit_prod = st.form_submit_button("Salvar no Meu Perfil", use_container_width=True)

        if submit_prod:
            if nome.strip() and url.strip() and alvo > 0:
                try:
                    with st.spinner("Validando link e salvando produto no seu perfil..."):
                        add_product(
                            name=nome.strip(),
                            url=url.strip(),
                            target_price=float(alvo),
                            user_id=user_id,
                        )
                    st.success(f"'{nome}' adicionado à sua lista!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao salvar produto: {e}")
            else:
                st.warning("Preencha o nome, link e preço alvo corretamente.")


# =====================================================================
# ÁREA PRINCIPAL DIVIDIDA EM ABAS DO PERFIL (+ ABA EXCLUSIVA DE ADMIN)
# =====================================================================
st.title(f"🏷️ Painel de Monitoramento — {current_user['display_name']}")

if not current_user.get("whatsapp_phone"):
    st.warning(
        "⚠️ **Configure seu número de WhatsApp** na aba **'📱 Meu WhatsApp & Perfil'** "
        "para receber as atualizações de preço diretamente no seu celular."
    )

tab_titles = [
    "📦 Meus Produtos Monitorados",
    "📈 Histórico & Alertas Enviados",
    "📱 Meu WhatsApp & Perfil",
]
if user_is_admin:
    tab_titles.append("Painel de Administração")

all_tabs = st.tabs(tab_titles)
tab_produtos = all_tabs[0]
tab_historico = all_tabs[1]
tab_whatsapp = all_tabs[2]
tab_admin = all_tabs[3] if user_is_admin else None

if user_is_admin and tab_admin is not None:
    with tab_admin:
        render_admin_panel(current_user)

# ---------------------------------------------------------------------
# ABA 1: GERENCIAMENTO DE PRODUTOS DO USUÁRIO (ISOLADO + PAGINAÇÃO + EXCLUSÃO SEGURA)
# ---------------------------------------------------------------------
with tab_produtos:
    col_title, col_action = st.columns([3, 1])
    with col_title:
        st.subheader("Sua Lista Pessoal de Monitoramento")
    with col_action:
        if st.button("🔄 Verificar Meus Preços Agora", type="primary", use_container_width=True):
            with st.spinner("Consultando lojas e verificando promoções para o seu perfil..."):
                alerts = check_all_products(user_id=user_id)
                st.session_state["last_alerts"] = alerts
            st.success("Verificação concluída!")
            st.rerun()

    # Exibe alertas recém-disparados com botão direto para o WhatsApp do usuário
    if st.session_state.get("last_alerts"):
        for alert in st.session_state["last_alerts"]:
            st.success(
                f"🚨 **Preço atingido para {alert['product_name']}!** "
                f"Atual: **R$ {alert['current_price']:.2f}** (Alvo: R$ {alert['target_price']:.2f}) — {alert['detail']}"
            )
            if alert.get("whatsapp_url"):
                st.link_button(
                    f"📲 Abrir Alerta no WhatsApp ({alert['product_name']})",
                    alert["whatsapp_url"],
                )

    produtos = get_user_products(user_id)

    if not produtos:
        st.info(
            "Você ainda não cadastrou nenhum produto no seu perfil. "
            "Use o formulário **'➕ Adicionar Produto'** na barra lateral!"
        )
    else:
        col_search, col_page_size = st.columns([3, 1])
        with col_search:
            user_search_q = st.text_input(
                "🔍 Filtrar meus produtos por nome ou loja",
                placeholder="Digite para filtrar...",
                key="user_prod_search",
            )
        with col_page_size:
            items_per_page = st.selectbox(
                "Itens por página",
                options=[5, 10, 20, 50],
                index=1,
                key="user_prod_page_size",
            )

        if user_search_q.strip():
            q_low = user_search_q.strip().lower()
            filtered_produtos = [
                p
                for p in produtos
                if q_low in (p.get("name") or "").lower()
                or q_low in (p.get("store_name") or "").lower()
                or q_low in (p.get("url") or "").lower()
            ]
        else:
            filtered_produtos = produtos

        total_items = len(filtered_produtos)
        total_pages = max((total_items + items_per_page - 1) // items_per_page, 1)

        if "user_prod_page" not in st.session_state:
            st.session_state["user_prod_page"] = 1
        if st.session_state["user_prod_page"] > total_pages:
            st.session_state["user_prod_page"] = total_pages

        current_page = int(st.session_state["user_prod_page"])
        start_idx = (current_page - 1) * items_per_page
        end_idx = min(start_idx + items_per_page, total_items)
        paginated_produtos = filtered_produtos[start_idx:end_idx]

        if total_items > items_per_page:
            nav_prev, nav_info, nav_next = st.columns([1, 2, 1])
            with nav_prev:
                if st.button("⬅️ Página Anterior", disabled=(current_page <= 1), use_container_width=True):
                    st.session_state["user_prod_page"] = current_page - 1
                    st.rerun()
            with nav_info:
                st.caption(
                    f"Página **{current_page}** de **{total_pages}** "
                    f"(exibindo {start_idx + 1}–{end_idx} de {total_items} produtos)"
                )
            with nav_next:
                if st.button("Próxima Página ➡️", disabled=(current_page >= total_pages), use_container_width=True):
                    st.session_state["user_prod_page"] = current_page + 1
                    st.rerun()

        for prod in paginated_produtos:
            pid = prod["id"]
            pname = prod["name"]
            purl = prod["url"]
            ptarget = float(prod["target_price"])
            plast = prod.get("last_price")
            pnotified = prod.get("last_notified_price")

            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([3, 2, 2, 2])

                with c1:
                    st.markdown(f"#### {pname}")
                    st.markdown(f"[🔗 Abrir página do produto na loja]({purl})")

                with c2:
                    if plast is not None:
                        delta_val = float(plast) - ptarget
                        status_icon = "🟢 Abaixo/Na Meta!" if float(plast) <= ptarget else "🟡 Acima da Meta"
                        st.metric(
                            "Último Preço Lido",
                            f"R$ {float(plast):.2f}",
                            delta=f"{delta_val:+.2f} vs alvo",
                            delta_color="inverse",
                        )
                        st.caption(status_icon)
                    else:
                        st.metric("Último Preço Lido", "Não verificado")
                        st.caption("Clique em 'Verificar Meus Preços Agora'")

                with c3:
                    st.metric("Seu Preço Alvo", f"R$ {ptarget:.2f}")
                    if pnotified is not None:
                        st.caption(f"✅ Último alerta: R$ {float(pnotified):.2f}")
                    else:
                        st.caption("🔔 Aguardando promoção")

                with c4:
                    # Link rápido de WhatsApp para este produto
                    if current_user.get("whatsapp_phone"):
                        msg_preview = format_whatsapp_message(
                            product_name=pname,
                            current_price=float(plast) if plast is not None else ptarget,
                            target_price=ptarget,
                            url=purl,
                            user_name=current_user["display_name"],
                        )
                        wa_link = generate_whatsapp_link(current_user["whatsapp_phone"], msg_preview)
                        st.link_button("📲 Enviar p/ WhatsApp", wa_link, use_container_width=True)

                    # Edição rápida de preço alvo
                    with st.popover("✏️ Alterar Alvo", use_container_width=True):
                        novo_alvo = st.number_input(
                            "Novo Preço Alvo (R$)",
                            min_value=0.1,
                            value=ptarget,
                            step=5.0,
                            format="%.2f",
                            key=f"target_input_{pid}",
                        )
                        if st.button("Salvar Novo Alvo", key=f"save_target_{pid}", use_container_width=True):
                            update_product_target(pid, user_id, float(novo_alvo))
                            st.success("Preço alvo atualizado!")
                            st.rerun()

                    # Botão de exclusão com confirmação obrigatória para evitar remoção acidental
                    if st.button("🗑️ Parar de Monitorar", key=f"ask_del_{pid}", use_container_width=True):
                        st.session_state["confirm_delete_id"] = pid
                        st.rerun()

                # Modal/Caixa de confirmação antes de excluir o produto
                if st.session_state.get("confirm_delete_id") == pid:
                    st.warning(
                        f"⚠️ **Confirmação de Exclusão:** Tem certeza que deseja remover **'{pname}'** da sua lista? "
                        "O monitoramento e todas as notificações deste produto serão interrompidos imediatamente."
                    )
                    col_yes, col_no, _ = st.columns([1, 1, 2])
                    with col_yes:
                        if st.button(
                            "✅ Sim, excluir produto",
                            key=f"confirm_del_{pid}",
                            type="primary",
                            use_container_width=True,
                        ):
                            if delete_product(pid, user_id):
                                st.session_state["confirm_delete_id"] = None
                                st.success(f"Produto '{pname}' removido com sucesso!")
                                st.rerun()
                    with col_no:
                        if st.button(
                            "❌ Cancelar",
                            key=f"cancel_del_{pid}",
                            use_container_width=True,
                        ):
                            st.session_state["confirm_delete_id"] = None
                            st.rerun()


# ---------------------------------------------------------------------
# ABA 2: HISTÓRICO DE PREÇOS E LOG DE NOTIFICAÇÕES WHATSAPP DO USUÁRIO
# ---------------------------------------------------------------------
with tab_historico:
    st.subheader("📈 Evolução de Preços dos Seus Produtos")
    historico = get_user_price_history(user_id=user_id, limit=40)

    if historico:
        df_hist = pd.DataFrame(historico)
        df_hist_display = df_hist.rename(
            columns={
                "checked_at": "Data e Hora",
                "product_name": "Produto",
                "price": "Preço Lido (R$)",
                "target_price": "Preço Alvo (R$)",
            }
        )[["Data e Hora", "Produto", "Preço Lido (R$)", "Preço Alvo (R$)"]]

        col_t, col_g = st.columns([1, 1])
        with col_t:
            st.markdown("**Últimas Leituras:**")
            st.dataframe(df_hist_display, use_container_width=True, hide_index=True)
        with col_g:
            st.markdown("**Gráfico de Variação:**")
            st.line_chart(
                df_hist_display,
                x="Data e Hora",
                y="Preço Lido (R$)",
                color="Produto",
            )
    else:
        st.caption("Nenhuma leitura registrada ainda para os seus produtos.")

    st.divider()
    st.subheader("📲 Histórico de Alertas do WhatsApp")
    notificacoes = get_user_notifications(user_id=user_id, limit=15)
    if notificacoes:
        df_notif = pd.DataFrame(notificacoes)[
            ["sent_at", "product_name", "price", "phone", "status"]
        ]
        df_notif.columns = ["Data/Hora", "Produto", "Preço (R$)", "WhatsApp Destino", "Status"]
        st.dataframe(df_notif, use_container_width=True, hide_index=True)
    else:
        st.caption("Nenhum alerta precisou ser disparado ainda.")


# ---------------------------------------------------------------------
# ABA 3: CONFIGURAÇÕES DO PERFIL E NÚMERO DE WHATSAPP
# ---------------------------------------------------------------------
with tab_whatsapp:
    st.subheader("📱 Configurar Meu WhatsApp e Preferências")
    st.markdown(
        "Configure abaixo o seu número de telefone para receber os alertas de queda de preço "
        "e promoções dos produtos que você monitora."
    )

    with st.form("profile_whatsapp_form"):
        edit_name = st.text_input("Seu Nome de Exibição", value=current_user.get("display_name", ""))
        edit_phone = st.text_input(
            "Número do WhatsApp (com DDD)",
            value=current_user.get("whatsapp_phone", ""),
            placeholder="Ex: 21999998888",
            help="Informe seu DDD + número (ex: 21999998888). O código 55 do Brasil é adicionado automaticamente.",
        )
        edit_enabled = st.checkbox(
            "Ativar notificações via WhatsApp para minha conta",
            value=bool(current_user.get("notifications_enabled", 1)),
        )
        edit_any_drop = st.checkbox(
            "Avisar também quando houver qualquer queda de preço relevante (mesmo acima da meta)",
            value=bool(current_user.get("notify_on_any_drop", 1)),
        )

        with st.expander("⚙️ Chave de API Automática (Opcional — apenas se possuir)"):
            edit_apikey = st.text_input(
                "Chave API WhatsApp (Opcional)",
                value=current_user.get("whatsapp_apikey", ""),
                placeholder="Deixe em branco para usar o envio direto do WhatsApp",
            )

        save_profile = st.form_submit_button("💾 Salvar Configurações de WhatsApp", type="primary")
        if save_profile:
            try:
                with st.spinner("Salvando suas preferências de WhatsApp..."):
                    updated = update_user_profile(
                        user_id=user_id,
                        display_name=edit_name,
                        whatsapp_phone=edit_phone,
                        whatsapp_apikey=edit_apikey,
                        notifications_enabled=edit_enabled,
                        notify_on_any_drop=edit_any_drop,
                    )
                st.session_state["user"] = updated
                st.success("✅ Suas configurações de WhatsApp foram atualizadas com sucesso!")
                st.rerun()
            except ValueError as err:
                st.error(str(err))

    st.divider()
    st.markdown("#### 🧪 Testar Notificação no Meu WhatsApp")
    if current_user.get("whatsapp_phone"):
        test_msg = (
            f"✅ *Teste do Rastreador de Preços*\n\n"
            f"Olá, *{current_user['display_name']}*! Seu número (+{current_user['whatsapp_phone']}) "
            f"está configurado para receber alertas dos seus produtos."
        )
        test_wa_link = generate_whatsapp_link(current_user["whatsapp_phone"], test_msg)
        st.link_button(
            f"📲 Enviar Mensagem de Teste para Meu WhatsApp (+{current_user['whatsapp_phone']})",
            test_wa_link,
            type="primary",
        )
    else:
        st.info("Salve seu número de WhatsApp acima para liberar o teste de envio.")
