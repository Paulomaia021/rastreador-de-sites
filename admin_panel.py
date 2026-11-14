from __future__ import annotations
import importlib
from pathlib import Path
import streamlit as st
import pandas as pd
import database
import notifier
import main

if (
    not hasattr(database, "admin_get_dashboard_kpis")
    or not hasattr(database, "MIN_PASSWORD_LENGTH")
    or not hasattr(database, "cleanup_old_logs")
):
    importlib.reload(database)
if not hasattr(notifier, "admin_retry_notification") or not hasattr(notifier, "send_recovery_code_whatsapp"):
    importlib.reload(notifier)
if not hasattr(main, "trigger_background_queue_worker") or not hasattr(main, "_is_retryable_scraper_error"):
    importlib.reload(main)

from database import (
    require_admin,
    admin_get_dashboard_kpis,
    admin_list_users,
    admin_set_user_active,
    admin_set_user_admin,
    admin_reset_user_password,
    admin_delete_user,
    get_user_products,
    admin_list_all_products,
    admin_toggle_product_status,
    get_system_settings,
    admin_update_system_settings,
    admin_get_queue_summary,
    admin_clear_finished_jobs,
    admin_get_price_analytics,
    admin_list_notifications,
    admin_get_system_logs,
    admin_get_audit_logs,
    admin_create_backup,
    admin_list_backups,
    admin_restore_backup,
    admin_export_data,
    cleanup_old_logs,
    MIN_PASSWORD_LENGTH,
)
from main import (
    trigger_background_queue_worker,
    process_job_queue_sync,
    is_worker_running,
)
from notifier import (
    get_integrations_status,
    admin_retry_notification,
    generate_whatsapp_link,
)


def _paginate_list(items: list, key_prefix: str, default_page_size: int = 20) -> list:
    """Renderiza controles de paginação para listas e tabelas administrativas extensas."""
    total = len(items)
    if total <= default_page_size:
        return items

    page_key = f"{key_prefix}_page"
    size_key = f"{key_prefix}_size"

    c_prev, c_info, c_size, c_next = st.columns([1, 2, 1, 1])
    with c_size:
        page_size = st.selectbox(
            "Por página",
            options=[10, 20, 50, 100],
            index=1 if default_page_size == 20 else 0,
            key=size_key,
            label_visibility="collapsed",
        )

    total_pages = max((total + page_size - 1) // page_size, 1)
    cur_page = min(max(int(st.session_state.get(page_key, 1)), 1), total_pages)
    st.session_state[page_key] = cur_page

    start = (cur_page - 1) * page_size
    end = min(start + page_size, total)

    with c_prev:
        if st.button("⬅️ Anterior", key=f"{key_prefix}_prev", disabled=(cur_page <= 1), use_container_width=True):
            st.session_state[page_key] = cur_page - 1
            st.rerun()
    with c_info:
        st.caption(f"Página **{cur_page}/{total_pages}** ({start + 1}–{end} de {total})")
    with c_next:
        if st.button("Próxima ➡️", key=f"{key_prefix}_next", disabled=(cur_page >= total_pages), use_container_width=True):
            st.session_state[page_key] = cur_page + 1
            st.rerun()

    return items[start:end]


def render_admin_panel(current_user: dict) -> None:
    """
    Renderiza o Painel de Administração completo dividido nas 8 seções oficiais.
    Valida a permissão administrativa no backend antes de qualquer operação.
    """
    admin_id = int(current_user["id"])
    try:
        require_admin(admin_id)
    except PermissionError as exc:
        st.error(str(exc))
        st.stop()

    if "admin_confirm_del_user" not in st.session_state:
        st.session_state["admin_confirm_del_user"] = None
    if "admin_confirm_restore" not in st.session_state:
        st.session_state["admin_confirm_restore"] = None

    st.markdown("## 👑 Painel de Administração Global")
    st.caption(
        "Controle global sobre usuários, produtos, fila de monitoramento, notificações WhatsApp, "
        "auditoria e backups da plataforma."
    )

    (
        sec_dashboard,
        sec_users,
        sec_products,
        sec_monitoring,
        sec_history,
        sec_notifications,
        sec_logs,
        sec_settings,
    ) = st.tabs(
        [
            "1. Dashboard",
            "2. Usuários",
            "3. Produtos",
            "4. Monitoramento",
            "5. Histórico de preços",
            "6. Notificações",
            "7. Logs e auditoria",
            "8. Configurações",
        ]
    )

    # =================================================================
    # SEÇÃO 1: DASHBOARD ADMINISTRATIVO (KPIs)
    # =================================================================
    with sec_dashboard:
        col_hdr, col_ref = st.columns([4, 1])
        with col_hdr:
            st.subheader("📊 Indicadores Gerais da Plataforma (KPIs)")
        with col_ref:
            if st.button("🔄 Atualizar KPIs", key="btn_refresh_kpis", use_container_width=True):
                st.rerun()

        kpis = admin_get_dashboard_kpis(admin_id)

        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        with r1c1:
            st.metric(
                "Total de Usuários",
                kpis["total_users"],
                delta=f"{kpis['active_users']} ativos / {kpis['inactive_users']} desativados",
            )
        with r1c2:
            st.metric(
                "Produtos Monitorados",
                kpis["total_products"],
                delta=f"{kpis['dropped_products']} em queda/meta",
            )
        with r1c3:
            st.metric(
                "Maior Desconto Encontrado",
                f"R$ {kpis['max_discount_value']:.2f}",
                delta=f"-{kpis['max_discount_pct']:.1f}% ({kpis['max_discount_product'][:18]})",
            )
        with r1c4:
            st.metric(
                "Status do Sistema",
                kpis["system_status"],
                delta=f"Última exec.: {kpis['last_global_run'][:19]}",
            )

        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        with r2c1:
            st.metric("Usuários Ativos", kpis["active_users"])
        with r2c2:
            st.metric("Usuários Desativados", kpis["inactive_users"])
        with r2c3:
            st.metric("Notificações WhatsApp Enviadas", kpis["sent_notifications"])
        with r2c4:
            st.metric(
                "Notificações com Falha",
                kpis["failed_notifications"],
                delta=f"{kpis['pending_notifications']} pendente(s)",
                delta_color="inverse",
            )

    # =================================================================
    # SEÇÃO 2: GERENCIAMENTO DE USUÁRIOS
    # =================================================================
    with sec_users:
        st.subheader("👥 Gerenciamento de Usuários e Perfis")
        users_list = admin_list_users(admin_id)

        df_users = pd.DataFrame(
            [
                {
                    "ID": u["id"],
                    "Nome de Exibição": u["display_name"],
                    "Login (Username)": u["username"],
                    "WhatsApp": f"+{u['whatsapp_phone']}" if u.get("whatsapp_phone") else "Não informado",
                    "Data de Cadastro": u["created_at"],
                    "Produtos Monitorados": u["products_count"],
                    "Nível": "👑 Admin" if u["is_admin"] == 1 else "👤 Usuário",
                    "Status da Conta": "🟢 Ativa" if u["is_active"] == 1 else "🔴 Desativada",
                }
                for u in users_list
            ]
        )
        st.dataframe(df_users, use_container_width=True, hide_index=True)

        st.markdown("#### ⚙️ Administrar Usuário Selecionado")
        user_options = {
            f"ID {u['id']} — {u['display_name']} (@{u['username']})": u for u in users_list
        }
        selected_label = st.selectbox("Selecione um usuário para gerenciar:", list(user_options.keys()))
        sel_user = user_options[selected_label]
        sel_uid = int(sel_user["id"])

        with st.container(border=True):
            c_info, c_pass, c_actions = st.columns([2, 2, 2])

            with c_info:
                st.markdown(f"**Detalhes de {sel_user['display_name']}**")
                st.write(f"- **ID:** `{sel_uid}` | **Login:** `{sel_user['username']}`")
                st.write(f"- **WhatsApp:** `{sel_user['whatsapp_phone'] or 'Não configurado'}`")
                st.write(f"- **Cadastrado em:** `{sel_user['created_at']}`")
                st.write(f"- **Produtos vinculados:** `{sel_user['products_count']}`")
                st.write(
                    f"- **Status:** {'🟢 Conta Ativa' if sel_user['is_active'] == 1 else '🔴 Conta Desativada'}"
                )

            with c_pass:
                st.markdown("**🔑 Redefinir Senha com Segurança**")
                new_pwd = st.text_input(
                    "Nova senha temporária",
                    type="password",
                    key=f"admin_reset_pwd_{sel_uid}",
                    placeholder=f"Mínimo {MIN_PASSWORD_LENGTH} caracteres",
                )
                if st.button("Salvar Nova Senha", key=f"btn_reset_pwd_{sel_uid}", use_container_width=True):
                    try:
                        admin_reset_user_password(admin_id, sel_uid, new_pwd)
                        st.success(f"Senha de @{sel_user['username']} redefinida com sucesso!")
                    except Exception as err:
                        st.error(str(err))

            with c_actions:
                st.markdown("**🛡️ Permissões e Status da Conta**")
                is_currently_active = sel_user["is_active"] == 1
                toggle_active_label = (
                    "🚫 Desativar Conta" if is_currently_active else "✅ Reativar Conta"
                )
                if st.button(toggle_active_label, key=f"btn_toggle_act_{sel_uid}", use_container_width=True):
                    try:
                        admin_set_user_active(admin_id, sel_uid, not is_currently_active)
                        st.success("Status da conta atualizado!")
                        st.rerun()
                    except Exception as err:
                        st.error(str(err))

                is_currently_admin = sel_user["is_admin"] == 1
                toggle_admin_label = (
                    "⬇️ Revogar Permissão Admin"
                    if is_currently_admin
                    else "👑 Promover a Administrador"
                )
                if st.button(toggle_admin_label, key=f"btn_toggle_adm_{sel_uid}", use_container_width=True):
                    try:
                        admin_set_user_admin(admin_id, sel_uid, not is_currently_admin)
                        st.success("Nível de permissão atualizado!")
                        st.rerun()
                    except Exception as err:
                        st.error(str(err))

                if st.button("🗑️ Excluir Usuário", key=f"btn_ask_del_usr_{sel_uid}", use_container_width=True):
                    st.session_state["admin_confirm_del_user"] = sel_uid
                    st.rerun()

            if st.session_state.get("admin_confirm_del_user") == sel_uid:
                st.warning(
                    f"⚠️ **Confirmação de Exclusão:** Tem certeza que deseja excluir a conta de "
                    f"**{sel_user['display_name']} (@{sel_user['username']})**? "
                    f"Todos os {sel_user['products_count']} produto(s) e históricos deste usuário serão removidos."
                )
                cy, cn, _ = st.columns([1, 1, 2])
                with cy:
                    if st.button(
                        "✅ Confirmar Exclusão Definitiva",
                        key=f"btn_confirm_del_usr_{sel_uid}",
                        type="primary",
                        use_container_width=True,
                    ):
                        try:
                            admin_delete_user(admin_id, sel_uid)
                            st.session_state["admin_confirm_del_user"] = None
                            st.success("Usuário e dados associados excluídos com sucesso!")
                            st.rerun()
                        except Exception as err:
                            st.error(str(err))
                with cn:
                    if st.button("❌ Cancelar", key=f"btn_cancel_del_usr_{sel_uid}", use_container_width=True):
                        st.session_state["admin_confirm_del_user"] = None
                        st.rerun()

            with st.expander(f"📦 Consultar produtos associados a {sel_user['display_name']} ({sel_user['products_count']})"):
                u_prods = get_user_products(sel_uid)
                if u_prods:
                    st.dataframe(
                        pd.DataFrame(u_prods)[
                            ["id", "name", "store_name", "target_price", "last_price", "monitoring_status", "url"]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.caption("Este usuário não possui produtos cadastrados.")

    # =================================================================
    # SEÇÃO 3: GERENCIAMENTO GLOBAL DE PRODUTOS
    # =================================================================
    with sec_products:
        st.subheader("📦 Gerenciamento Global de Produtos Monitorados")

        f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 2, 2])
        with f1:
            user_filter_map = {"Todos os Usuários": None}
            for u in users_list:
                user_filter_map[f"{u['display_name']} (@{u['username']})"] = u["id"]
            sel_u_filter = st.selectbox("Filtrar por Usuário", list(user_filter_map.keys()))
        with f2:
            sel_store_filter = st.selectbox(
                "Filtrar por Loja",
                ["Todas", "Mercado Livre", "Amazon", "Books to Scrape", "Magazine Luiza", "KaBuM!", "Shopee", "Outra Loja"],
            )
        with f3:
            sel_status_filter = st.selectbox(
                "Status do Monitoramento",
                ["Todos", "ativo", "pausado", "erro"],
            )
        with f4:
            max_price_filter = st.number_input("Preço Máximo (R$, 0=Todos)", min_value=0.0, value=0.0, step=50.0)
        with f5:
            search_q = st.text_input("Pesquisar Nome ou URL", placeholder="Ex: notebook, livro...")

        all_prods = admin_list_all_products(
            admin_user_id=admin_id,
            user_filter=user_filter_map[sel_u_filter],
            store_filter=sel_store_filter,
            status_filter=sel_status_filter,
            search_query=search_q,
            max_price=max_price_filter if max_price_filter > 0 else None,
        )

        dup_items = [p for p in all_prods if p.get("is_duplicate")]
        if dup_items:
            st.info(
                f"🔍 **Identificação de Duplicados:** Foram encontrados **{len(dup_items)}** registro(s) "
                "de produtos cujo link é monitorado por mais de um usuário simultaneamente (as listas individuais permanecem isoladas)."
            )

        if all_prods:
            paged_prods = _paginate_list(all_prods, key_prefix="adm_prods", default_page_size=20)
            df_global_prods = pd.DataFrame(
                [
                    {
                        "ID": p["id"],
                        "Produto": p["name"],
                        "Loja": p.get("store_name", ""),
                        "Preço Atual (R$)": p.get("last_price"),
                        "Preço Anterior (R$)": p.get("previous_price"),
                        "Preço Alvo (R$)": p.get("target_price"),
                        "Usuário Responsável": f"{p.get('owner_name') or 'Admin'} (@{p.get('owner_username') or 'admin'})",
                        "Data de Inclusão": p.get("created_at") or "-",
                        "Última Verificação": p.get("last_checked_at") or "Nunca",
                        "Status": p.get("monitoring_status") or "ativo",
                        "Duplicado?": f"🔁 Sim ({p['duplicate_count']} perfis)" if p.get("is_duplicate") else "Não",
                        "Link": p.get("url", ""),
                    }
                    for p in paged_prods
                ]
            )
            st.dataframe(
                df_global_prods,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Link": st.column_config.LinkColumn("Link Monitorado", display_text="Abrir URL 🔗"),
                },
            )

            with st.expander("🔎 Inspecionar Detalhes ou Alterar Status de um Produto"):
                prod_map = {f"ID {p['id']} — {p['name']} ({p.get('owner_username')})": p for p in all_prods}
                sel_prod_label = st.selectbox("Selecione o produto:", list(prod_map.keys()))
                sel_p = prod_map[sel_prod_label]
                st.json(
                    {
                        "id": sel_p["id"],
                        "name": sel_p["name"],
                        "url": sel_p["url"],
                        "store_name": sel_p.get("store_name"),
                        "owner": f"{sel_p.get('owner_name')} (@{sel_p.get('owner_username')})",
                        "target_price": sel_p.get("target_price"),
                        "last_price": sel_p.get("last_price"),
                        "previous_price": sel_p.get("previous_price"),
                        "monitoring_status": sel_p.get("monitoring_status"),
                        "duplicate_count": sel_p.get("duplicate_count"),
                        "error_count": sel_p.get("error_count", 0),
                        "last_error": sel_p.get("last_error", ""),
                    }
                )
                cur_st = sel_p.get("monitoring_status", "ativo")
                next_st = "pausado" if cur_st == "ativo" else "ativo"
                if st.button(
                    f"{'⏸️ Pausar' if next_st == 'pausado' else '▶️ Reativar'} Monitoramento deste Produto",
                    key=f"btn_adm_prod_st_{sel_p['id']}",
                ):
                    admin_toggle_product_status(admin_id, int(sel_p["id"]), next_st)
                    st.success(f"Status do produto alterado para '{next_st}'!")
                    st.rerun()
        else:
            st.caption("Nenhum produto encontrado para os filtros selecionados.")

    # =================================================================
    # SEÇÃO 4: CONTROLE GLOBAL DE MONITORAMENTO & FILA (JOB QUEUE)
    # =================================================================
    with sec_monitoring:
        st.subheader("⚡ Controle Global de Monitoramento e Fila de Processamento")
        settings = get_system_settings()
        is_paused = settings.get("monitoring_paused") == "1"
        queue_data = admin_get_queue_summary(admin_id, limit=50)
        q_counts = queue_data["counts"]
        worker_active = is_worker_running()

        mc1, mc2, mc3, mc4 = st.columns(4)
        with mc1:
            st.metric("⏳ Aguardando na Fila", q_counts.get("aguardando", 0))
        with mc2:
            st.metric("🔄 Em Processamento", q_counts.get("processando", 0))
        with mc3:
            st.metric("✅ Concluídos", q_counts.get("concluido", 0))
        with mc4:
            st.metric("❌ Com Falha", q_counts.get("falha", 0))

        st.caption(
            f"**Última execução global:** `{settings.get('last_global_run') or 'Nunca executado'}` | "
            f"**Worker em segundo plano:** `{'🟢 Executando' if worker_active else '⚪ Ocioso'}` | "
            f"**Estado Global:** `{'⏸️ PAUSADO' if is_paused else '🟢 ATIVO'}`"
        )

        b1, b2, b3, b4 = st.columns(4)
        with b1:
            if st.button(
                "🚀 Executar Verificação Global (Enfileirar)",
                type="primary",
                use_container_width=True,
                disabled=is_paused,
            ):
                res = trigger_background_queue_worker(admin_id)
                st.success(
                    f"{res['enqueued']} produto(s) adicionado(s) à fila! "
                    "Processamento iniciado gradualmente em segundo plano."
                )
                st.rerun()
        with b2:
            if st.button("⚡ Processar Fila Agora (Síncrono)", use_container_width=True, disabled=is_paused):
                with st.spinner("Processando fila de tarefas com controle de tentativas..."):
                    trigger_res = admin_get_queue_summary(admin_id)
                    if trigger_res["counts"].get("aguardando", 0) == 0:
                        trigger_background_queue_worker(admin_id)
                    sync_res = process_job_queue_sync()
                st.success(
                    f"Fila processada: {sync_res['processed']} tarefa(s) "
                    f"({sync_res['succeeded']} sucesso, {sync_res['failed']} falha)."
                )
                st.rerun()
        with b3:
            pause_btn_label = "▶️ Retomar Monitoramento Global" if is_paused else "⏸️ Pausar Monitoramento Global"
            if st.button(pause_btn_label, use_container_width=True):
                admin_update_system_settings(
                    admin_id, {"monitoring_paused": "0" if is_paused else "1"}
                )
                st.rerun()
        with b4:
            if st.button("🧹 Limpar Jobs Concluídos/Falhos", use_container_width=True):
                removed = admin_clear_finished_jobs(admin_id)
                st.info(f"{removed} registro(s) limpos da fila.")
                st.rerun()

        st.markdown("#### 📋 Tarefas na Fila de Processamento (Job Queue)")
        if queue_data["jobs"]:
            df_jobs = pd.DataFrame(
                [
                    {
                        "Job ID": j["id"],
                        "Produto": j.get("product_name") or f"ID {j['product_id']}",
                        "Usuário": j.get("user_name") or f"ID {j['user_id']}",
                        "Status": j["status"],
                        "Tentativas": f"{j['attempts']}/{j['max_attempts']}",
                        "Preço Lido (R$)": j.get("result_price"),
                        "HTTP": j.get("http_status"),
                        "Tempo (ms)": j.get("response_time_ms"),
                        "Criado em": j.get("created_at"),
                        "Finalizado em": j.get("finished_at") or "-",
                        "Erro": j.get("error_message") or "",
                    }
                    for j in queue_data["jobs"]
                ]
            )
            st.dataframe(df_jobs, use_container_width=True, hide_index=True)
        else:
            st.caption("Nenhuma tarefa registrada na fila no momento.")

    # =================================================================
    # SEÇÃO 5: HISTÓRICO DE PREÇOS E ANÁLISES
    # =================================================================
    with sec_history:
        st.subheader("📈 Histórico de Preços e Análises Globais")

        all_p_for_hist = admin_list_all_products(admin_id)
        hist_filter_map = {"🌐 Visão Global (Todos os Produtos)": None}
        for p in all_p_for_hist:
            hist_filter_map[f"ID {p['id']} — {p['name']} ({p.get('store_name')})"] = int(p["id"])

        sel_hist_prod = st.selectbox("Escopo da Análise:", list(hist_filter_map.keys()))
        chosen_pid = hist_filter_map[sel_hist_prod]

        analytics = admin_get_price_analytics(admin_id, product_id=chosen_pid, limit=100)

        if analytics["product_stats"]:
            st.markdown("#### 📊 Estatísticas por Produto (Menor/Maior Preço Histórico e Alterações)")
            df_stats = pd.DataFrame(
                [
                    {
                        "ID": s["product_id"],
                        "Produto": s["product_name"],
                        "Loja": s.get("store_name") or "-",
                        "Menor Preço Histórico (R$)": s.get("min_price"),
                        "Maior Preço Histórico (R$)": s.get("max_price"),
                        "Preço Atual (R$)": s.get("last_price"),
                        "Variação vs Topo (%)": f"{s.get('overall_variation_pct', 0.0):+.2f}%",
                        "Queda Máxima (R$)": f"R$ {s.get('drop_from_max', 0.0):.2f}",
                        "Alterações Detectadas": s.get("changes_detected") or 0,
                        "Total de Consultas": s.get("readings_count") or 0,
                    }
                    for s in analytics["product_stats"]
                ]
            )
            st.dataframe(df_stats, use_container_width=True, hide_index=True)

        if analytics["biggest_drops"]:
            st.markdown("#### 🏆 Produtos com Maiores Quedas de Preço")
            df_drops = pd.DataFrame(analytics["biggest_drops"])[
                ["product_name", "store_name", "max_price", "last_price", "drop_from_max", "overall_variation_pct"]
            ]
            df_drops.columns = [
                "Produto",
                "Loja",
                "Maior Preço (R$)",
                "Preço Atual (R$)",
                "Economia (R$)",
                "Variação (%)",
            ]
            st.dataframe(df_drops, use_container_width=True, hide_index=True)

        if analytics["history"]:
            df_h = pd.DataFrame(
                [
                    {
                        "Data e Horário": h["checked_at"],
                        "Produto": h["product_name"],
                        "Loja de Origem": h.get("store_name") or "-",
                        "Preço Registrado (R$)": h["price"],
                        "Preço Anterior (R$)": h.get("previous_price"),
                        "Variação (%)": h.get("variation_pct", 0.0),
                        "Usuário": h.get("user_name") or "Admin",
                    }
                    for h in analytics["history"]
                ]
            )
            ch_t, ch_g = st.columns([1, 1])
            with ch_t:
                st.markdown("**Leituras Registradas:**")
                st.dataframe(df_h, use_container_width=True, hide_index=True)
            with ch_g:
                st.markdown("**Gráfico de Evolução dos Preços:**")
                st.line_chart(
                    df_h,
                    x="Data e Horário",
                    y="Preço Registrado (R$)",
                    color="Produto",
                )
        else:
            st.caption("Ainda não há leituras de preço registradas para o filtro selecionado.")

    # =================================================================
    # SEÇÃO 6: CENTRAL DE NOTIFICAÇÕES WHATSAPP
    # =================================================================
    with sec_notifications:
        st.subheader("📲 Central de Notificações WhatsApp")

        nf1, nf2 = st.columns(2)
        with nf1:
            sel_notif_status = st.selectbox(
                "Filtrar por Status da Mensagem:",
                ["Todos", "Pendente", "Enviada", "Entregue", "Falha"],
            )
        with nf2:
            sel_notif_user = st.selectbox(
                "Filtrar por Usuário Destinatário:",
                list(user_filter_map.keys()),
                key="notif_user_filter",
            )

        notifs = admin_list_notifications(
            admin_user_id=admin_id,
            status_filter=sel_notif_status,
            user_filter=user_filter_map[sel_notif_user],
            limit=100,
        )

        if notifs:
            invalid_phones = [n for n in notifs if not n.get("phone_valid")]
            if invalid_phones:
                st.warning(
                    f"⚠️ Foram identificadas **{len(invalid_phones)}** notificação(ões) com número de WhatsApp ausente ou inválido."
                )

            paged_notifs = _paginate_list(notifs, key_prefix="adm_notifs", default_page_size=20)
            df_notifs = pd.DataFrame(
                [
                    {
                        "ID": n["id"],
                        "Data/Horário": n["sent_at"],
                        "Usuário Destinatário": n.get("user_name") or f"ID {n['user_id']}",
                        "WhatsApp": n.get("phone") or "Não informado",
                        "Número Válido?": "✅ Sim" if n.get("phone_valid") else "❌ Inválido",
                        "Produto Relacionado": n.get("product_name") or f"ID {n['product_id']}",
                        "Preço Identificado (R$)": n["price"],
                        "Status da Mensagem": n.get("delivery_status") or "Enviada",
                        "Provedor": n.get("provider") or "whatsapp_direct",
                        "Duplicada?": "🔁 Sim" if n.get("is_duplicate") else "Não",
                        "Tentativas": n.get("retry_count", 0),
                        "Erro da API": n.get("error_message") or "-",
                    }
                    for n in paged_notifs
                ]
            )
            st.dataframe(df_notifs, use_container_width=True, hide_index=True)

            st.markdown("#### 🔁 Reenviar Mensagem ou Abrir Link Direto")
            notif_map = {
                f"ID {n['id']} — {n.get('user_name')} | {n.get('product_name')} (Status: {n.get('delivery_status')})": n
                for n in notifs
            }
            sel_n_label = st.selectbox("Selecione uma notificação:", list(notif_map.keys()))
            sel_n = notif_map[sel_n_label]

            cn1, cn2 = st.columns(2)
            with cn1:
                if st.button("🚀 Reenviar Notificação Agora", key=f"btn_retry_notif_{sel_n['id']}", use_container_width=True):
                    try:
                        with st.spinner("Reenviando notificação via WhatsApp..."):
                            res = admin_retry_notification(admin_id, int(sel_n["id"]))
                        st.success(f"Reenvio processado! Novo status: {res['delivery_status']} ({res['detail']})")
                        st.rerun()
                    except Exception as err:
                        st.error(str(err))
            with cn2:
                wa_direct = generate_whatsapp_link(sel_n.get("phone", ""), sel_n.get("message", ""))
                st.link_button("📲 Abrir Mensagem no WhatsApp", wa_direct, use_container_width=True)
        else:
            st.caption("Nenhuma notificação registrada para os filtros informados.")

    # =================================================================
    # SEÇÃO 7: LOGS, AUDITORIA E DIAGNÓSTICO
    # =================================================================
    with sec_logs:
        st.subheader("🛡️ Logs Técnicos, Auditoria Administrativa e Diagnóstico")

        sub_sys_logs, sub_audit_logs = st.tabs(
            ["🖥️ Logs de Diagnóstico (Scraper, HTTP & WhatsApp)", "📋 Trilha de Auditoria Administrativa"]
        )

        with sub_sys_logs:
            lf1, lf2, lf3, lf4 = st.columns(4)
            with lf1:
                sel_ev_type = st.selectbox("Tipo de Evento", ["Todos", "scraper", "whatsapp", "queue", "auth", "system"])
            with lf2:
                sel_ev_status = st.selectbox("Status do Evento", ["Todos", "sucesso", "erro", "aviso"])
            with lf3:
                sel_ev_user = st.selectbox("Usuário Relacionado", list(user_filter_map.keys()), key="log_user_filter")
            with lf4:
                sel_ev_date = st.text_input("Filtrar por Data (AAAA-MM-DD)", placeholder="Ex: 2026-11-13")

            sys_logs = admin_get_system_logs(
                admin_user_id=admin_id,
                event_type=sel_ev_type,
                status_filter=sel_ev_status,
                user_filter=user_filter_map[sel_ev_user],
                date_filter=sel_ev_date,
                limit=200,
            )
            if sys_logs:
                paged_slogs = _paginate_list(sys_logs, key_prefix="adm_slogs", default_page_size=20)
                df_slogs = pd.DataFrame(
                    [
                        {
                            "ID": l["id"],
                            "Data e Horário": l["created_at"],
                            "Tipo": l["event_type"],
                            "Status": l["status"],
                            "Usuário": l.get("username") or (f"ID {l['user_id']}" if l.get("user_id") else "-"),
                            "Produto": l.get("product_name") or (f"ID {l['product_id']}" if l.get("product_id") else "-"),
                            "Código HTTP": l.get("http_status") or "-",
                            "Tempo Resposta (ms)": l.get("response_time_ms") or "-",
                            "Tentativas": l.get("attempts") or 1,
                            "URL Consultada": l.get("url") or "-",
                            "Mensagem / Diagnóstico": l.get("message") or "",
                        }
                        for l in paged_slogs
                    ]
                )
                st.dataframe(df_slogs, use_container_width=True, hide_index=True)
            else:
                st.caption("Nenhum log técnico encontrado para os filtros selecionados.")

        with sub_audit_logs:
            af1, af2 = st.columns(2)
            with af1:
                sel_aud_action = st.selectbox(
                    "Filtrar por Ação Administrativa",
                    [
                        "Todas",
                        "PROMOVER_ADMIN",
                        "REVOGAR_ADMIN",
                        "ATIVAR_USUARIO",
                        "DESATIVAR_USUARIO",
                        "RESETAR_SENHA",
                        "EXCLUIR_USUARIO",
                        "DISPARAR_FILA_GLOBAL",
                        "ATUALIZAR_CONFIGURACOES",
                        "GERAR_BACKUP",
                        "RESTAURAR_BACKUP",
                        "REENVIAR_NOTIFICACAO",
                    ],
                )
            with af2:
                sel_aud_date = st.text_input("Data da Auditoria (AAAA-MM-DD)", placeholder="Ex: 2026-11-13")

            aud_logs = admin_get_audit_logs(
                admin_user_id=admin_id,
                action_filter=sel_aud_action,
                date_filter=sel_aud_date,
                limit=200,
            )
            if aud_logs:
                paged_alogs = _paginate_list(aud_logs, key_prefix="adm_alogs", default_page_size=20)
                df_alogs = pd.DataFrame(
                    [
                        {
                            "ID": a["id"],
                            "Data/Horário": a["created_at"],
                            "Administrador": f"@{a['admin_username']} (ID {a['admin_id']})",
                            "Ação": a["action"],
                            "Usuário Alvo ID": a.get("target_user_id") or "-",
                            "Detalhes da Operação": a["details"],
                        }
                        for a in paged_alogs
                    ]
                )
                st.dataframe(df_alogs, use_container_width=True, hide_index=True)
            else:
                st.caption("Nenhum registro de auditoria encontrado.")

    # =================================================================
    # SEÇÃO 8: CONFIGURAÇÕES GERAIS, INTEGRAÇÕES, EXPORTAÇÕES E BACKUP
    # =================================================================
    with sec_settings:
        st.subheader("⚙️ Configurações Gerais, Integrações e Backups")
        cur_settings = get_system_settings()

        col_cfg, col_int = st.columns([1, 1])

        with col_cfg:
            st.markdown("#### ⏱️ Parâmetros Globais de Monitoramento")
            with st.form("admin_global_settings_form"):
                interval_min = st.number_input(
                    "Intervalo entre verificações automáticas (minutos)",
                    min_value=5,
                    max_value=1440,
                    value=int(cur_settings.get("check_interval_minutes", "60") or 60),
                    step=5,
                )
                max_retries_val = st.number_input(
                    "Limite máximo de tentativas em caso de falha HTTP",
                    min_value=1,
                    max_value=10,
                    value=int(cur_settings.get("max_retries", "3") or 3),
                    step=1,
                )
                delay_req_val = st.number_input(
                    "Pausa entre requisições da fila (segundos)",
                    min_value=0.0,
                    max_value=10.0,
                    value=float(cur_settings.get("delay_between_requests_sec", "1.0") or 1.0),
                    step=0.5,
                )
                retention_days_val = st.number_input(
                    "Retenção de logs e histórico de fila (dias)",
                    min_value=7,
                    max_value=365,
                    value=int(cur_settings.get("log_retention_days", "90") or 90),
                    step=15,
                )
                save_global_cfg = st.form_submit_button("💾 Salvar Configurações Globais", type="primary")
                if save_global_cfg:
                    admin_update_system_settings(
                        admin_id,
                        {
                            "check_interval_minutes": str(interval_min),
                            "max_retries": str(max_retries_val),
                            "delay_between_requests_sec": str(delay_req_val),
                            "log_retention_days": str(retention_days_val),
                        },
                    )
                    cleanup_old_logs(int(retention_days_val))
                    st.success("Configurações globais e política de retenção salvas com sucesso!")
                    st.rerun()

        with col_int:
            st.markdown("#### 🔌 Status das Integrações")
            integrations = get_integrations_status()
            st.dataframe(pd.DataFrame(integrations), use_container_width=True, hide_index=True)

        st.divider()
        col_bkp, col_exp = st.columns([1, 1])

        with col_bkp:
            st.markdown("#### 💾 Backup e Restauração Segura do Banco de Dados")
            st.caption(
                f"**Último backup realizado:** `{cur_settings.get('last_backup_at') or 'Nenhum'}` "
                f"(`{cur_settings.get('last_backup_file') or '-'}`)"
            )

            if st.button("📦 Gerar Novo Backup do Banco SQLite Agora", type="primary", use_container_width=True):
                with st.spinner("Gerando snapshot consistente do banco de dados..."):
                    bkp_info = admin_create_backup(admin_id)
                st.success(
                    f"Backup '{bkp_info['filename']}' criado com sucesso ({bkp_info['size_bytes']} bytes)!"
                )
                st.rerun()

            backups_list = admin_list_backups(admin_id)
            if backups_list:
                sel_bkp_file = st.selectbox(
                    "Backups disponíveis:",
                    [b["filename"] for b in backups_list],
                )
                sel_bkp_path = Path(next(b["path"] for b in backups_list if b["filename"] == sel_bkp_file))
                if sel_bkp_path.exists():
                    with open(sel_bkp_path, "rb") as bf:
                        st.download_button(
                            label=f"⬇️ Baixar Arquivo {sel_bkp_file}",
                            data=bf.read(),
                            file_name=sel_bkp_file,
                            mime="application/octet-stream",
                            use_container_width=True,
                        )

                if st.button("♻️ Restaurar Banco a partir deste Backup", use_container_width=True):
                    st.session_state["admin_confirm_restore"] = sel_bkp_file
                    st.rerun()

                if st.session_state.get("admin_confirm_restore") == sel_bkp_file:
                    st.warning(
                        f"⚠️ **Confirmação de Restauração:** Deseja substituir o banco atual pelo backup `{sel_bkp_file}`?"
                    )
                    rc1, rc2 = st.columns(2)
                    with rc1:
                        if st.button("✅ Confirmar Restauração", type="primary", use_container_width=True):
                            try:
                                with st.spinner("Validando integridade e restaurando banco de dados..."):
                                    admin_restore_backup(admin_id, sel_bkp_file)
                                st.session_state["admin_confirm_restore"] = None
                                st.success("Banco de dados restaurado com segurança!")
                                st.rerun()
                            except Exception as err:
                                st.error(str(err))
                    with rc2:
                        if st.button("❌ Cancelar Restauração", use_container_width=True):
                            st.session_state["admin_confirm_restore"] = None
                            st.rerun()

        with col_exp:
            st.markdown("#### 📤 Exportar Dados da Plataforma (CSV / JSON)")
            exp_entity = st.selectbox(
                "Selecione a tabela para exportar:",
                ["users", "products", "price_history", "notifications", "logs"],
            )
            exp_format = st.radio("Formato de exportação:", ["csv", "json"], horizontal=True)

            exported_content = admin_export_data(admin_id, exp_entity, exp_format)
            mime_type = "text/csv" if exp_format == "csv" else "application/json"
            st.download_button(
                label=f"📥 Baixar {exp_entity}.{exp_format}",
                data=exported_content.encode("utf-8"),
                file_name=f"export_{exp_entity}.{exp_format}",
                mime=mime_type,
                use_container_width=True,
            )
            with st.expander("Pré-visualizar dados exportados"):
                st.code(exported_content[:1500] or "Sem dados", language="json" if exp_format == "json" else "text")
