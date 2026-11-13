import streamlit as st
import pandas as pd
from database import (
    init_db,
    create_user,
    authenticate_user,
    get_user_by_id,
    update_user_profile,
    add_product,
    get_user_products,
    update_product_target,
    delete_product,
    get_user_price_history,
    get_user_notifications,
    normalize_whatsapp_phone,
)
from main import check_all_products
from notifier import (
    format_whatsapp_message,
    generate_whatsapp_link,
    send_whatsapp_message,
)

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


def render_auth_screen() -> None:
    """Exibe a tela de login e criação de conta individual."""
    st.title("🏷️ Rastreador Pessoal de Preços com WhatsApp")
    st.markdown(
        "Monitore seus produtos favoritos em um perfil **100% isolado e privado** e "
        "receba alertas de promoção diretamente no seu **WhatsApp**."
    )

    col_left, col_center, col_right = st.columns([1, 2, 1])
    with col_center:
        tab_login, tab_register = st.tabs(["🔑 Entrar na Conta", "📝 Criar Meu Perfil"])

        with tab_login:
            with st.form("login_form"):
                st.subheader("Acessar meu painel")
                username = st.text_input("Usuário", placeholder="Ex: paulo")
                password = st.text_input("Senha", type="password")
                submit_login = st.form_submit_button("Entrar", use_container_width=True)

                if submit_login:
                    user = authenticate_user(username, password)
                    if user:
                        st.session_state["user"] = user
                        st.session_state["last_alerts"] = []
                        st.rerun()
                    else:
                        st.error("Usuário ou senha incorretos. Verifique seus dados ou crie um perfil.")

        with tab_register:
            with st.form("register_form"):
                st.subheader("Criar novo perfil individual")
                new_display_name = st.text_input("Seu Nome", placeholder="Ex: Paulo Maia")
                new_username = st.text_input("Nome de Usuário (para login)", placeholder="Ex: paulo")
                new_password = st.text_input("Crie uma Senha", type="password")
                new_whatsapp = st.text_input(
                    "Seu WhatsApp (com DDD)",
                    placeholder="Ex: 21999998888",
                    help="Número onde você receberá os alertas de queda de preço.",
                )
                new_apikey = st.text_input(
                    "Chave API WhatsApp CallMeBot (Opcional)",
                    placeholder="Deixe em branco se quiser configurar depois",
                )
                submit_register = st.form_submit_button("Criar Conta e Entrar", use_container_width=True)

                if submit_register:
                    if not new_username.strip() or not new_password.strip():
                        st.warning("Preencha pelo menos o nome de usuário e a senha.")
                    else:
                        try:
                            created = create_user(
                                username=new_username,
                                password=new_password,
                                display_name=new_display_name,
                                whatsapp_phone=new_whatsapp,
                                whatsapp_apikey=new_apikey,
                            )
                            st.session_state["user"] = created
                            st.success("Perfil criado com sucesso!")
                            st.rerun()
                        except ValueError as err:
                            st.error(str(err))


# Se não estiver logado, mostra apenas a tela de autenticação
if not st.session_state["user"]:
    render_auth_screen()
    st.stop()

# Usuário autenticado: recarrega dados frescos do banco garantindo isolamento
current_user = get_user_by_id(st.session_state["user"]["id"])
if not current_user:
    st.session_state["user"] = None
    st.rerun()
st.session_state["user"] = current_user
user_id = current_user["id"]

# =====================================================================
# BARRA LATERAL: PERFIL, CADASTRO DE PRODUTO E LOGOUT
# =====================================================================
with st.sidebar:
    st.markdown(f"### 👤 Olá, {current_user['display_name']}!")
    phone_display = (
        f"+{current_user['whatsapp_phone']}"
        if current_user.get("whatsapp_phone")
        else "⚠️ Não configurado"
    )
    st.caption(f"📱 WhatsApp ativo: **{phone_display}**")

    if st.button("🚪 Sair da Conta", use_container_width=True):
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
# ÁREA PRINCIPAL DIVIDIDA EM ABAS DO PERFIL
# =====================================================================
st.title(f"🏷️ Painel de Monitoramento — {current_user['display_name']}")

if not current_user.get("whatsapp_phone"):
    st.warning(
        "⚠️ **Configure seu número de WhatsApp** na aba **'📱 Meu WhatsApp & Perfil'** "
        "para receber as atualizações de preço diretamente no seu celular."
    )

tab_produtos, tab_historico, tab_whatsapp = st.tabs(
    [
        "📦 Meus Produtos Monitorados",
        "📈 Histórico & Alertas Enviados",
        "📱 Meu WhatsApp & Perfil",
    ]
)

# ---------------------------------------------------------------------
# ABA 1: GERENCIAMENTO DE PRODUTOS DO USUÁRIO (ISOLADO + EXCLUSÃO SEGURA)
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
        for prod in produtos:
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
            "Número do WhatsApp (com DDI + DDD)",
            value=current_user.get("whatsapp_phone", ""),
            placeholder="Ex: 5521999998888",
            help="Informe apenas números (ex: 55 + DDD + seu número).",
        )
        edit_apikey = st.text_input(
            "Chave API CallMeBot para Envio Automático em Background (Opcional)",
            value=current_user.get("whatsapp_apikey", ""),
            placeholder="Ex: 1234567",
            help="Permite que o sistema envie mensagens sozinho para o seu WhatsApp sem precisar clicar.",
        )
        edit_enabled = st.checkbox(
            "Ativar notificações via WhatsApp para minha conta",
            value=bool(current_user.get("notifications_enabled", 1)),
        )
        edit_any_drop = st.checkbox(
            "Avisar também quando houver qualquer queda de preço relevante (mesmo acima da meta)",
            value=bool(current_user.get("notify_on_any_drop", 1)),
        )

        save_profile = st.form_submit_button("💾 Salvar Configurações de WhatsApp", type="primary")
        if save_profile:
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

    st.divider()
    col_test, col_help = st.columns([1, 1])

    with col_test:
        st.markdown("#### 🧪 Testar Notificação no Meu WhatsApp")
        if current_user.get("whatsapp_phone"):
            test_msg = (
                f"✅ *Teste do Rastreador de Preços*\n\n"
                f"Olá, *{current_user['display_name']}*! Seu número (+{current_user['whatsapp_phone']}) "
                f"está configurado para receber alertas dos seus produtos."
            )
            test_wa_link = generate_whatsapp_link(current_user["whatsapp_phone"], test_msg)

            if st.button("🚀 Disparar Teste Automático via API", use_container_width=True):
                res = send_whatsapp_message(
                    phone=current_user["whatsapp_phone"],
                    message=test_msg,
                    user_apikey=current_user.get("whatsapp_apikey", ""),
                )
                if res["method"] != "whatsapp_direct":
                    st.success(f"Mensagem enviada automaticamente! ({res['detail']})")
                else:
                    st.info(
                        "Alerta preparado! Para envio 100% automático em segundo plano sem clicar, "
                        "veja ao lado como ativar a chave gratuita CallMeBot, ou clique no botão abaixo:"
                    )

            st.link_button(
                "📲 Abrir Mensagem de Teste no Meu WhatsApp",
                test_wa_link,
                use_container_width=True,
            )
        else:
            st.info("Salve seu número de WhatsApp acima para liberar o teste de envio.")

    with col_help:
        with st.expander("💡 Como ativar o envio 100% automático e gratuito no WhatsApp (CallMeBot)?", expanded=True):
            st.markdown(
                """
                Se você quiser que o servidor envie a mensagem sozinho para o seu WhatsApp assim que o preço cair:
                1. Adicione o contato **`+34 644 52 74 88`** na agenda do seu celular.
                2. Envie a seguinte mensagem para ele no WhatsApp:
                   `I allow callmebot to send me messages`
                3. Em poucos segundos, o bot responderá com a sua **API Key** gratuita (ex: `1234567`).
                4. Cole essa chave no campo **"Chave API CallMeBot"** ao lado e clique em **Salvar**!
                """
            )
