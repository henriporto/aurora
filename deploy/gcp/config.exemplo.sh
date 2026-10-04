# Configuração dos scripts de deploy no GCP.
# Copie para deploy/gcp/config.sh e ajuste.

# ID do projeto no Google Cloud (Console > seletor de projetos)
PROJETO="seu-projeto-id"
REGIAO="us-central1"
ZONA="us-central1-a"

NOME_VM="..."
TIPO_MAQUINA="e2-standard-2"
DISCO_GB="60"

NOME_IP="...-ip"
TAG_REDE="..."

# Bucket para cópias diárias do usuarios.db
BUCKET_BACKUP="gs://..."

# E-mail que recebe os alertas do Cloud Monitoring
EMAIL_ALERTAS="..."

# leis.db local, usado só pelo 02_enviar_banco.sh
BANCO_LOCAL="dados/leis.db"

# Dataset do Hugging Face de onde a VM baixa o banco
HF_REPO_BANCO="henriporto/aurora"

# Repositório no GitHub autorizado a fazer deploy (dono/nome)
GITHUB_REPO="henriporto/aurora"
