# Política de segurança

## Versões cobertas

A Aurora não tem versões numeradas com suporte separado. As correções de segurança valem para:

| Versão | Coberta |
| :--- | :--- |
| Branch `main` | sim |
| Servidor público (`https://mcp.auroravoto.com.br`) | sim |
| Commits antigos e forks | não |

## Como reportar uma vulnerabilidade

**Não abra uma issue pública.** Uma falha divulgada antes da correção coloca em risco quem usa o serviço.

Use um destes canais:

- **Pelo GitHub (preferido):** na aba [Security](https://github.com/henriporto/aurora/security) do repositório, clique em **Report a vulnerability**. O relato fica visível só para os mantenedores.
- **Por e-mail:** henriqueporto@id.uff.br, com o assunto "Segurança - Aurora".

Inclua, se possível:

- o que a falha permite fazer e qual o impacto;
- os passos para reproduzir;
- a versão ou o commit testado, e se o teste foi local ou no servidor público.

## O que esperar

- **Confirmação de recebimento** em até 7 dias.
- **Avaliação:** se o relato for aceito, você é avisado do prazo previsto para a correção; se não for, recebe a explicação do motivo.
- **Correção e divulgação:** a falha é corrigida na `main` e publicada no servidor. Depois disso, o relato pode ser divulgado, com crédito a quem reportou, se a pessoa quiser.

A Aurora é um projeto acadêmico mantido por uma pessoa. Não há programa de recompensas.

## Escopo

Interessam relatos sobre:

- acesso sem login, ou com o papel de outra pessoa (por exemplo, usar ferramentas de administrador);
- acesso a dados de outros usuários: e-mails, histórico de consultas, tokens;
- formas de burlar a cota diária ou o controle de acesso;
- escrita no banco de dados, que é somente leitura, ou leitura de arquivos do servidor;
- uso do servidor para acessar outros sistemas, como na leitura de documentos sob demanda;
- falhas no fluxo de login com o Google.

Ficam fora do escopo:

- erros nos dados legislativos, que vêm das bases abertas da Câmara e do Senado: abra uma issue comum;
- respostas incorretas da IA do usuário a partir de dados corretos;
- vulnerabilidades em dependências que não afetam a Aurora na prática;
- indisponibilidade causada por volume de requisições.

## Ao testar

- Prefira testar em uma instalação local. O README explica como rodar o servidor.
- No servidor público, use só a sua própria conta e não acesse dados de outras pessoas.
- Não faça testes de carga nem de negação de serviço: o servidor é pequeno e atende usuários reais.
