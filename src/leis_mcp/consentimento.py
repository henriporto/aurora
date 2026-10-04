"""Tela de consentimento do FastMCP em português.

Só o HTML é trocado: token anti-fraude, cookies e envio do formulário continuam
os do FastMCP, e os campos `txn_id`, `csrf_token`, `submit` e `action` precisam
manter nomes e valores. O domínio de destino fica à vista; o endereço completo
vai para "Detalhes técnicos".
"""

from __future__ import annotations

import html
from urllib.parse import urlparse

from fastmcp.server.auth.oauth_proxy import consent as _consent
from fastmcp.utilities.ui import (
    BUTTON_STYLES,
    DETAIL_BOX_STYLES,
    DETAILS_STYLES,
    INFO_BOX_STYLES,
    REDIRECT_SECTION_STYLES,
    TOOLTIP_STYLES,
    create_logo,
    create_page,
)

_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; base-uri 'none'"

_ESTILOS = """
    .pergunta { font-size: 1.05rem; line-height: 1.5; }
    .origem {
        border-radius: 8px;
        padding: 8px 16px;
        margin-bottom: 16px;
        font-size: 14px;
        text-align: center;
    }
    .origem.verificada { background: #ecfdf5; border: 1px solid #6ee7b7; color: #065f46; }
    .origem.desconhecida { background: #fffbeb; border: 1px solid #fcd34d; color: #92400e; }
    .proximo-passo { font-size: 0.9rem; color: #6b7280; margin-bottom: 1.5rem; }
"""


def pagina_de_consentimento(
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    txn_id: str,
    csrf_token: str,
    client_name: str | None = None,
    title: str = "Conectar ao Aurora",
    server_name: str | None = None,
    server_icon_url: str | None = None,
    server_website_url: str | None = None,
    client_website_url: str | None = None,
    csp_policy: str | None = None,
    is_cimd_client: bool = False,
    cimd_domain: str | None = None,
) -> str:
    """Mesma assinatura de `create_consent_html` do FastMCP, que esta função substitui."""
    e = html.escape
    servidor = server_name or "Aurora"
    cliente = e(client_name or client_id)
    servidor_html = e(servidor)
    if server_website_url:
        servidor_html = (
            f'<a href="{e(server_website_url)}" target="_blank" '
            f'rel="noopener noreferrer" class="server-name-link">{servidor_html}</a>'
        )

    if is_cimd_client and cimd_domain:
        origem = (
            '<div class="origem verificada">&#x2713; Pedido verificado: vem de '
            f"<strong>{e(cimd_domain)}</strong></div>"
        )
    else:
        destino = urlparse(redirect_uri).hostname or redirect_uri
        origem = (
            '<div class="origem desconhecida">O acesso será entregue a '
            f"<strong>{e(destino)}</strong>. Só permita se você reconhece este endereço.</div>"
        )

    detalhes = [
        ("Aplicativo", e(client_name or client_id)),
        ("Site do aplicativo", e(client_website_url or "não informado")),
        ("ID do aplicativo", e(client_id)),
        ("Endereço de retorno", e(redirect_uri)),
        (
            "Permissões pedidas",
            ", ".join(e(s) for s in scopes) if scopes else "nenhuma",
        ),
    ]
    linhas = "\n".join(
        f'<div class="detail-row"><div class="detail-label">{rotulo}:</div>'
        f'<div class="detail-value">{valor}</div></div>'
        for rotulo, valor in detalhes
    )

    conteudo = f"""
        <div class="container">
            {create_logo(icon_url=server_icon_url, alt_text=servidor)}
            <h1>Conectar ao {e(servidor)}</h1>
            <div class="info-box">
                <p class="pergunta">O <strong>{cliente}</strong> quer se conectar ao
                <strong>{servidor_html}</strong>.</p>
            </div>
            {origem}
            <p class="proximo-passo">No próximo passo você entra com sua conta Google.
            Nenhuma senha é compartilhada com o {cliente}.</p>
            <form id="consentForm" method="POST" action="">
                <input type="hidden" name="txn_id" value="{e(txn_id)}" />
                <input type="hidden" name="csrf_token" value="{e(csrf_token)}" />
                <input type="hidden" name="submit" value="true" />
                <div class="button-group">
                    <button type="submit" name="action" value="approve" class="btn-approve">Continuar</button>
                    <button type="submit" name="action" value="deny" class="btn-deny">Cancelar</button>
                </div>
            </form>
            <details>
                <summary>Detalhes técnicos</summary>
                <div class="detail-box">
                    {linhas}
                </div>
            </details>
        </div>
        <div class="help-link-container">
            <span class="help-link">
                Por que estou vendo isto?
                <span class="tooltip">
                    O {e(servidor)} pede sua confirmação antes do login sempre que um
                    aplicativo quer se conectar a ele. Assim nenhum programa
                    consegue acesso sem que você veja e autorize.
                </span>
            </span>
        </div>
    """

    pagina = create_page(
        content=conteudo,
        title=title,
        additional_styles=INFO_BOX_STYLES
        + REDIRECT_SECTION_STYLES
        + DETAILS_STYLES
        + DETAIL_BOX_STYLES
        + BUTTON_STYLES
        + TOOLTIP_STYLES
        + _ESTILOS,
        csp_policy=_CSP if csp_policy is None else csp_policy,
    )
    return pagina.replace('<html lang="en">', '<html lang="pt-BR">', 1)


def instalar() -> None:
    """Faz o FastMCP usar a página em português.

    Não há parâmetro público: troca o nome que o módulo `consent` importou.
    Conferir ao atualizar o FastMCP, se `create_consent_html` mudar de argumentos.
    """
    _consent.create_consent_html = pagina_de_consentimento
