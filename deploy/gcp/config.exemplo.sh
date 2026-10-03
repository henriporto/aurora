# Configuração dos scripts de deploy no GCP.
# Copie para deploy/gcp/config.sh (fora do git) e ajuste.

# ID do projeto no Google Cloud (Console > seletor de projetos)
PROJETO="seu-projeto-id"

# us-central1: região do crédito gratuito, preço mais baixo e próxima de onde
# partem as chamadas do claude.ai.
REGIAO="us-central1"
ZONA="us-central1-a"

NOME_VM="leis-mcp"
# 2 vCPU e 8 GB: o processo pica em ~3,6 GB; o restante vira cache de disco do
# banco, que é o que mantém a busca no inteiro teor rápida.
TIPO_MAQUINA="e2-standard-2"
# No pico de uma troca de banco: versão em uso + anterior + nova (3 x 12,1 GB
# desde 19/09/2026, quando o banco ganhou os discursos e as votações
# simbólicas), mais imagens Docker (~5 GB) e sistema. Dá ~46 GB dos 60: ainda
# cabe, mas sem folga para uma quarta versão. O 02_enviar_banco.sh confere o
# espaço antes de enviar e aborta se faltar.
DISCO_GB="60"

NOME_IP="leis-mcp-ip"
TAG_REDE="leis-mcp"

# Bucket para cópias diárias do usuarios.db (nome globalmente único).
BUCKET_BACKUP="gs://seu-projeto-id-leis-backup"

# E-mail que recebe os alertas do Cloud Monitoring (04_monitoramento.sh).
EMAIL_ALERTAS="voce@gmail.com"

# Onde fica o leis.db de origem na sua máquina.
# Usado só pelo 02_enviar_banco.sh, que envia um banco que você ainda não
# publicou. O caminho normal é o 02_banco_do_hf.sh, em que a VM baixa sozinha.
# Caminho relativo à raiz do projeto ou absoluto.
BANCO_LOCAL="dados/leis.db"

# Repositório (dataset) do Hugging Face de onde a VM baixa o banco.
HF_REPO_BANCO="henriporto/aurora"

# Repositório no GitHub autorizado a fazer deploy, no formato dono/nome.
# Lido pelo 00_github_actions.sh, que restringe a federação de identidade a ele:
# sem essa condição, qualquer repositório do GitHub conseguiria credenciais
# deste projeto.
GITHUB_REPO="henriporto/aurora"
