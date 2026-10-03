subir o banco:

uv run scripts/comprimir_banco.py --verificar
uvx --from huggingface_hub hf upload henriporto/aurora dados/publicar . --repo-type dataset

entrar na VM (os comandos "na VM" abaixo rodam nesta pasta):

gcloud compute ssh aurora --zone us-central1-a
cd /srv/leis/app/deploy


usuários e aprovações (na VM):

sudo docker compose exec leis-mcp leis-admin usuarios listar
sudo docker compose exec leis-mcp leis-admin usuarios listar --papel pendente
sudo docker compose exec leis-mcp leis-admin usuarios aprovar pessoa@gmail.com
sudo docker compose exec leis-mcp leis-admin usuarios adicionar pessoa@gmail.com
sudo docker compose exec leis-mcp leis-admin usuarios adicionar pessoa@gmail.com --papel usuario --cota 50 --nome "Nome"
sudo docker compose exec leis-mcp leis-admin usuarios papel pessoa@gmail.com bloqueado
sudo docker compose exec leis-mcp leis-admin usuarios cota pessoa@gmail.com 100

papéis: admin, usuario, pendente, bloqueado
cota: número, ilimitada ou padrao
enquanto o app OAuth estiver "Em teste", a pessoa também precisa estar nos usuários de teste do Google:
https://console.cloud.google.com/auth/audience?project=aurora-39284ry3298u


uso (na VM):

sudo docker compose exec leis-mcp leis-admin relatorio --desde 2026-10-01
sudo docker compose exec leis-mcp leis-admin relatorio --email pessoa@gmail.com
sudo docker compose exec leis-mcp leis-admin chamadas --limite 20
sudo docker compose exec leis-mcp leis-admin chamadas --email pessoa@gmail.com
sudo docker compose exec leis-mcp leis-admin exportar /dados/chamadas.csv --desde 2026-10-01 --anonimizar


saúde do servidor (na VM):

sudo docker compose ps
sudo docker compose logs -f leis-mcp
sudo docker compose logs -f caddy
sudo docker stats --no-stream
df -h /
sudo tail /var/log/leis-backup.log
sudo docker compose restart leis-mcp


da minha máquina, sem entrar na VM:

curl -s https://mcp.auroravoto.com.br/saude
gh run list -R henriporto/aurora


painéis no navegador:

painel leis-mcp (chamadas, duração, fila, CPU, memória, disco): https://console.cloud.google.com/monitoring/dashboards?project=aurora-39284ry3298u
alertas: https://console.cloud.google.com/monitoring/alerting?project=aurora-39284ry3298u
logs: https://console.cloud.google.com/logs/query;query=logName%3D%22projects%2Faurora-39284ry3298u%2Flogs%2Fdocker_leis%22?project=aurora-39284ry3298u
deploys: https://github.com/henriporto/aurora/actions
gasto dos créditos: https://console.cloud.google.com/billing
