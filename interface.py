import streamlit as st
import pandas as pd
from database import init_db, add_product, get_all_products, get_connection
from main import check_all_products

# Inicializa o banco
init_db()

st.set_page_config(page_title="Rastreador de Preços", page_icon="🏷️", layout="wide")
st.title("🏷️ Rastreador & Monitor de Preços")

# Barra lateral para cadastrar produtos
with st.sidebar:
    st.header("Cadastrar Produto")
    nome = st.text_input("Nome do Produto")
    url = st.text_input("URL do Produto")
    alvo = st.number_input("Preço Alvo (R$)", min_value=0.0, step=10.0, format="%.2f")

    if st.button("Salvar Produto"):
        if nome and url and alvo > 0:
            add_product(nome, url, alvo)
            st.success(f"'{nome}' cadastrado com sucesso!")
        else:
            st.error("Preencha todos os campos corretamente.")

# Área principal: Lista de produtos
st.subheader("Produtos Monitorados")
produtos = get_all_products()

if produtos:
    # Converte os dados do SQLite em uma tabela visual elegante
    df = pd.DataFrame(produtos)[["id", "name", "target_price", "last_notified_price"]]
    df.columns = ["ID", "Produto", "Preço Alvo (R$)", "Último Notificado (R$)"]
    st.dataframe(df, use_container_width=True)
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔄 Checar Preços Agora"):
            with st.spinner("Varrendo lojas e atualizando preços..."):
                check_all_products()
            st.success("Checagem concluída!")
            st.rerun()
else:
    st.info("Nenhum produto cadastrado ainda. Use o menu lateral para adicionar!")

# Histórico de preços
st.subheader("📈 Histórico Recente de Leituras")
with get_connection() as conn:
    historico = conn.execute("""
        SELECT h.checked_at as Data, p.name as Produto, h.price as Preco
        FROM price_history h
        JOIN products p ON p.id = h.product_id
        ORDER BY h.id DESC LIMIT 10
    """).fetchall()
    if historico:
        st.table([dict(r) for r in historico])