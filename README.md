# 🏷️ Rastreador de Preços com Notificações via WhatsApp

Um sistema web completo, multiusuário e profissional em **Python** para monitorar preços de produtos em e-commerces (Mercado Livre, Books to Scrape e lojas virtuais com metadados OpenGraph/Schema.org), com fila assíncrona de processamento, painel administrativo global, alta segurança e alertas automáticos via **WhatsApp**.

---

## ✨ Funcionalidades Principais

### 👤 Perfis de Usuários e Experiência Pessoal
* **Isolamento Total:** Cada usuário gerencia seus próprios produtos, histórico e notificações em um ambiente 100% privado.
* **Segurança e Criptografia:** Senhas protegidas com hash PBKDF2-HMAC-SHA256 e Salt criptográfico aleatório.
* **Sessões Inteligentes:** Sessão persistente deslizante com logout automático após **15 minutos de inatividade**.
* **Recuperação de Conta Segura:** Redefinição de senha em 2 etapas com código de 6 dígitos gerado via CSPRNG (`secrets`), validade de 15 minutos, proteção contra força bruta e sem exposição de dados ou tokens na URL.
* **Catálogo e Paginação:** Visualização organizada com busca rápida por nome/loja e paginação configurável.
* **Edição e Exclusão Segura:** Edição ágil de preços-alvo e modal de confirmação para exclusão de itens.

### 📲 Alertas Inteligentes no WhatsApp
* **Disparo Automático:** Avisos enviados quando o preço atinge o alvo desejado ou em caso de quedas expressivas.
* **Prevenção Anti-Spam:** Sistema anti-duplicação que evita renotificar pelo mesmo preço já informado.
* **Múltiplos Provedores Suportados:**
  * Envio Direto (Link `wa.me` de 1 clique, sem custos).
  * [CallMeBot](https://www.callmebot.com/) (API gratuita para mensagens automáticas).
  * Gateways / Webhooks próprios (Evolution API, Z-API, etc.).
  * Twilio WhatsApp Business API.

### 👑 Painel Administrativo Global (`admin_panel.py`)
Controle total da plataforma dividido em 8 seções especializadas:
1. **Dashboard de KPIs:** Métricas em tempo real de usuários ativos/desativados, produtos monitorados, maiores descontos da plataforma e saúde dos disparos.
2. **Gerenciamento de Usuários:** Ativação/desativação de contas, concessão/revogação de privilégios de administrador e redefinição de senhas.
3. **Produtos Globais:** Monitoramento de todos os itens cadastrados no sistema com filtro por loja, status, usuário e detecção de **URLs monitoradas por mais de um perfil**.
4. **Monitoramento e Job Queue:** Fila assíncrona de verificação com controles para pausar/retomar, disparo manual e limpeza de histórico.
5. **Histórico e Análises:** Registro cronológico de variações, gráficos de linha por produto, menor/maior preço histórico e ranking dos produtos com maiores quedas de preço.
6. **Central de Notificações:** Auditoria completa das mensagens disparadas, status de entrega, identificação de números inválidos e botão de reenvio imediato.
7. **Logs Técnicos e Auditoria:** Rastreabilidade completa com logs de diagnóstico (Scraper, HTTP status, tempo de resposta em ms, WhatsApp) e trilha de auditoria para ações administrativas.
8. **Configurações, Backups e Exportação:** Ajuste de intervalo e retries, política de retenção de logs, geração e restauração segura de snapshots SQLite com teste de integridade e exportação completa em **CSV** ou **JSON**.

---

## 🛡️ Segurança e Resiliência

* **Proteção contra Força Bruta (Rate Limiting):** Bloqueio temporário de 15 minutos após 5 tentativas incorretas de login ou 5 falhas no código de recuperação.
* **Mitigação de SSRF e XSS:** Validação estrita de URLs bloqueando esquemas perigosos, `localhost`, loopback e faixas de IP privadas ou reservadas.
* **Concorrência Segura (Worker Lock):** Bloqueio não-bloqueante (`_WORKER_LOCK`) e reivindicação atômica de tarefas na fila via `BEGIN IMMEDIATE` no SQLite, prevenindo race conditions e execuções duplicadas.
* **Auto-Recuperação de Tarefas:** Recuperação automática de jobs órfãos/travados há mais de 10 minutos no status `processando`.
* **Scraping Resiliente:** Reutilização de conexões TCP/TLS via `requests.Session()`, cache de requisições por URL na mesma rodada e cancelamento imediato de retries para erros HTTP permanentes (400, 401, 403, 404, 410, 422).
* **Parser de Preços Robusto (`clean_price`):** Normalização avançada para moedas nacionais e internacionais, tratamento de separadores de milhar sem centavos (ex: `R$ 1.299` -> `1299.0`), ofertas "De/Por" e remoção de textos de parcelamento.

---

## 🛠️ Tecnologias Utilizadas

* **Linguagem:** Python 3.10+
* **Interface Web:** [Streamlit](https://streamlit.io/)
* **Manipulação e Análise de Dados:** Pandas
* **Web Scraping:** Requests & BeautifulSoup4
* **Banco de Dados:** SQLite3 configurado em **WAL Mode** (`PRAGMA journal_mode = WAL`, `synchronous = NORMAL`, `busy_timeout = 20000`) com 10 índices de alta performance e fechamento automático de conexões
* **Segurança:** PBKDF2-HMAC-SHA256, CSPRNG (`secrets`) e comparações em tempo constante (`hmac.compare_digest`)
* **Testes:** Unittest (cobertura completa de migrações, segurança, regras de negócio e estabilidade)

---

## 🚀 Como Executar o Projeto Localmente

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

### 4. (Opcional) Configure as variáveis de ambiente
Caso queira utilizar disparos automáticos por API em vez do envio direto pelo WhatsApp Web:
```bash
cp .env.example .env
```
Edite o arquivo `.env` preenchendo as chaves do provedor desejado (CallMeBot, Twilio ou Gateway próprio).

### 5. Execute a aplicação web
```bash
streamlit run app.py
```
O painel abrirá automaticamente no seu navegador em `http://localhost:8501`.

---

## 🧪 Executando os Testes Automatizados

O projeto possui uma suíte completa de testes unitários e de integração cobrindo controle de acesso, validação de senhas e URLs, rate limiting, isolamento de perfis, parser de preços, WAL mode, índices SQLite e concorrência dos workers:

```bash
python test_audit_fixes.py
```

---

## ⚙️ Estrutura do Projeto

```text
rastreador-de-sites/
├── app.py               # Interface web principal (login, produtos, perfil e visualizações)
├── admin_panel.py       # Painel administrativo global (8 seções de controle e auditoria)
├── database.py          # Modelagem SQLite (WAL mode, índices, autenticação, fila e sessões)
├── scraper.py           # Mecanismo de scraping, validação de URLs e parser inteligente de preços
├── notifier.py          # Formatação de mensagens e integração com APIs de WhatsApp
├── main.py              # Orquestrador da fila (Job Queue), worker em background e regras de alerta
├── test_audit_fixes.py  # Suíte de testes automatizados do sistema
├── requirements.txt     # Dependências do projeto
├── .env.example         # Modelo de configuração para APIs de WhatsApp
└── README.md            # Documentação técnica do projeto
```

---

## 👨‍💻 Autor

Desenvolvido por **Paulo Cezar Maia de Lima Gomes**.  
Sinta-se à vontade para conectar, reportar problemas ou sugerir melhorias!
