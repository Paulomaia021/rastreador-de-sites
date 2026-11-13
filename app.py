import streamlit as st
import pandas as pd
from database import init_db, add_product, get_all_products, get_connection
from main import check_all_products

# 1. Configuração da página do Streamlit
st.set_page_config(
    page_title="Rastreador de Preços",
    page_icon="🏷️",
    layout="wide"
)

# Inicializa o banco de dados se ainda não existir
init_db()

st.title("🏷️ Painel de Monitoramento de Preços")
st.markdown("Cadastre produtos de e-commerces e monitore quedas de preço automaticamente.")

# 2. Barra lateral para cadastro de novos produtos
with st.sidebar:
    st.header("➕ Cadastrar Novo Produto")
    with st.form("form_novo_produto", clear_on_submit=True):
        nome = st.text_input("Nome / Descrição", placeholder="Ex: Livro Python Fluente")
        url = st.text_input("URL do Produto", placeholder="https://...")
        alvo = st.number_input("Preço Alvo Desejado (R$)", min_value=0.1, step=5.0, format="%.2f")
        submit = st.form_submit_button("Salvar Produto")

        if submit:
            if nome.strip() and url.strip() and alvo > 0:
                try:
                    add_product(nome.strip(), url.strip(), float(alvo))
                    st.success(f"'{nome}' cadastrado com sucesso!")
                except Exception as e:
                    st.error(f"Erro ao salvar: {e}")
            else:
                st.warning("Preencha todos os campos corretamente.")

# 3. Painel Principal: Produtos cadastrados
col_header, col_btn = st.columns([4, 1])
with col_header:
    st.subheader("📦 Produtos Monitorados")
with col_btn:
    if st.button("🔄 Checar Preços Agora", use_container_width=True):
        with st.spinner("Buscando preços e atualizando histórico..."):
            check_all_products()
        st.success("Verificação concluída!")
        st.rerun()

produtos = get_all_products()

if produtos:
    # Formata os dados para exibição na tabela
    tabela_produtos = []
    for p in produtos:
        ultimo_alerta = f"R$ {p['last_notified_price']:.2f}" if p['last_notified_price'] else "Ainda não"
        tabela_produtos.append({
            "ID": p["id"],
            "Produto": p["name"],
            "Preço Alvo": f"R$ {p['target_price']:.2f}",
            "Último Alerta Enviado": ultimo_alerta,
            "Link": p["url"]
        })

    df_produtos = pd.DataFrame(tabela_produtos)
    st.dataframe(
        df_produtos,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("Link do Produto", display_text="Abrir Loja 🔗")
        }
    )
else:
    st.info("Nenhum produto cadastrado no momento. Use o menu lateral à esquerda para cadastrar!")

st.divider()

# 4. Histórico recente de leituras com gráfico
st.subheader("📈 Histórico Recente de Verificações")

with get_connection() as conn:
    historico = conn.execute("""
        SELECT h.checked_at as 'Data e Hora', p.name as 'Produto', h.price as 'Preço (R$)'
        FROM price_history h
        JOIN products p ON p.id = h.product_id
        ORDER BY h.id DESC
        LIMIT 25
    """).fetchall()

if historico:
    df_hist = pd.DataFrame([dict(r) for r in historico])
    
    col_tabela, col_grafico = st.columns([1, 1])
    with col_tabela:
        st.markdown("**Últimas 25 Leituras:**")
        st.dataframe(df_hist, use_container_width=True, hide_index=True)

    with col_grafico:
        st.markdown("**Variação de Preço:**")
        # Gráfico simples de linha agrupado por produto
        st.line_chart(
            df_hist,
            x="Data e Hora",
            y="Preço (R$)",
            color="Produto"
        )
else:
    st.caption("O histórico será exibido aqui assim que a primeira checagem for realizada.")
