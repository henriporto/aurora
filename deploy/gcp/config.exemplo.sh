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
# Banco (5,5 GB) + uma versão anterior para voltar + imagens Docker + sistema.
DISCO_GB="60"

NOME_IP="leis-mcp-ip"
TAG_REDE="leis-mcp"

# Bucket para cópias diárias do usuarios.db (nome globalmente único).
BUCKET_BACKUP="gs://seu-projeto-id-leis-backup"

# Onde fica o leis.db de origem na sua máquina.
# Caminho relativo à raiz do projeto ou absoluto.
BANCO_LOCAL="dados/leis.db"
