# 🏷️ Rastreador de Preços com Notificações via WhatsApp

Um sistema web completo e multiusuário em **Python** para monitorar preços de produtos em e-commerces (Mercado Livre, Books to Scrape e lojas com OpenGraph/Schema.org) e enviar alertas inteligentes de queda de preço diretamente para o **WhatsApp** de cada usuário.

---

## ✨ Funcionalidades Principais

* 👥 **Perfis de Usuários 100% Isolados:** Cada pessoa cria sua conta com senha criptografada (PBKDF2-HMAC-SHA256) e gerencia sua própria lista privada de monitoramento.
* 📦 **Gerenciamento de Produtos:** Adicione links de produtos, defina o preço-alvo desejado, edite metas a qualquer momento e remova produtos com confirmação de segurança.
* 📲 **Alertas Inteligentes no WhatsApp:**
  * Notificação personalizada contendo o nome do produto, preço atual, meta e link direto da loja.
  * Botão de 1 clique para abrir a mensagem formatada no WhatsApp (Web, iOS e Android).
  * Filtro anti-spam: evita notificações duplicadas para o mesmo valor já avisado.
* 📈 **Histórico e Gráficos:** Acompanhamento visual da variação de preços de cada produto ao longo do tempo.
* 🌐 **Interface Web Moderna:** Desenvolvida em **Streamlit**, totalmente responsiva para desktop e dispositivos móveis.

---

## 🛠️ Tecnologias Utilizadas

* **Linguagem:** Python 3.10+
* **Interface Frontend:** [Streamlit](https://streamlit.io/)
* **Manipulação de Dados:** Pandas
* **Web Scraping:** Requests & BeautifulSoup4
* **Banco de Dados:** SQLite3 (nativo com chaves estrangeiras e integridade referencial)
* **Segurança:** Hash criptográfico PBKDF2 com Salt aleatório

---

## 🚀 Como Rodar o Projeto Localmente

### 1. Clone o repositório
```bash
git clone https://github.com/Paulomaia021/rastreador-de-sites.git
cd rastreador-de-sites
```

### 2. Crie e ative o ambiente virtual
```bash
# Linux / macOS:
python3 -m venv .venv
source .venv/bin/activate

# Windows:
python -m venv .venv
.venv\Scripts\activate
```

### 3. Instale as dependências
```bash
pip install -r requirements.txt
```

### 4. Execute a aplicação
```bash
streamlit run app.py
```
O painel abrirá automaticamente no seu navegador em `http://localhost:8501`.

---

## ⚙️ Estrutura do Projeto

```text
rastreador-de-sites/
├── app.py              # Interface gráfica web em Streamlit (autenticação, painel e gráficos)
├── database.py         # Modelagem SQLite, criptografia de senhas e persistência dos perfis
├── scraper.py          # Extrator e limpador de preços (HTML Parsing)
├── notifier.py         # Módulo de formatação e disparo de mensagens via WhatsApp
├── main.py             # Orquestrador de verificação de preços e regras de disparo
├── requirements.txt    # Dependências do projeto
└── .env.example        # Modelo de variáveis de ambiente opcionais (APIs de WhatsApp)
```

---

## 👨‍💻 Autor

Desenvolvido por **Paulo Cezar Maia de Lima Gomes**.  
Sinta-se à vontade para conectar e contribuir com o projeto!
