"""Fontes governamentais complementares (gratuitas):
- Portal da Transparência (CGU): CEIS e CNEP — sanções e processos de responsabilização.
  Chave gratuita: https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email
- TCU — Certidões consolidadas de Pessoa Jurídica (TCU, CNJ/CNIA improbidade, CEIS, CNEP). Sem chave.
- Dados cadastrais do CNPJ (dados abertos da Receita Federal, via espelhos públicos BrasilAPI /
  minhareceita.org): razão social e sócios, usados para pesquisar pelo nome no DJEN.
"""
from __future__ import annotations

import logging

from ..documento import Documento
from ..http import ErroFonte, requisitar

log = logging.getLogger("consulta")


class Transparencia:
    nome = "transparencia"
    BASE = "https://api.portaldatransparencia.gov.br/api-de-dados"

    def __init__(self, api_key: str):
        self.headers = {"chave-api-dados": api_key, "Accept": "application/json"}

    def pessoa(self, doc: Documento) -> dict:
        """CPF -> nome + vínculos federais (servidor, benefícios, sanções, licitações...).
        CNPJ -> indicadores da empresa. Só retorna o nome se a pessoa constar em alguma base federal."""
        if doc.tipo not in ("CPF", "CNPJ"):
            return {}
        rota, param = ("pessoa-fisica", "cpf") if doc.tipo == "CPF" else ("pessoa-juridica", "cnpj")
        d = requisitar("GET", f"{self.BASE}/{rota}", headers=self.headers, params={param: doc.valor}, cache_horas=168)
        if isinstance(d, list):
            d = d[0] if d else {}
        if not isinstance(d, dict):
            return {}
        flags = sorted(k for k, v in d.items() if v is True)
        return {"nome": d.get("nome") or d.get("razaoSocial"), "nis": d.get("nis"), "indicadores": flags}

    def sancoes(self, doc: Documento) -> list[dict]:
        if doc.tipo not in ("CPF", "CNPJ"):
            return []
        saida = []
        for cadastro in ("ceis", "cnep"):
            pagina = 1
            while True:
                dados = requisitar("GET", f"{self.BASE}/{cadastro}", headers=self.headers,
                                   params={"codigoSancionado": doc.valor, "pagina": pagina}, cache_horas=24)
                if not dados:
                    break
                for s in dados:
                    saida.append({
                        "cadastro": cadastro.upper(),
                        "tipo_sancao": (s.get("tipoSancao") or {}).get("descricaoResumida"),
                        "orgao_sancionador": (s.get("orgaoSancionador") or {}).get("nome"),
                        "numero_processo": s.get("numeroProcesso"),
                        "data_inicio": s.get("dataInicioSancao"),
                        "data_fim": s.get("dataFimSancao"),
                        "fundamentacao": "; ".join(f.get("descricao", "") for f in s.get("fundamentacao") or []),
                        "valor_multa": s.get("valorMulta"),
                    })
                pagina += 1
        return saida


def certidoes_tcu(doc: Documento) -> dict:
    """Consulta consolidada do TCU (somente CNPJ)."""
    if doc.tipo != "CNPJ":
        return {}
    d = requisitar("GET", f"https://certidoes-apf.apps.tcu.gov.br/api/rest/publico/certidoes/{doc.valor}",
                   params={"seEmitirPDF": "false"}, cache_horas=24)
    certs = []
    for c in d.get("certidoes") or []:
        certs.append({
            "emissor": c.get("emissor"),
            "cadastro": c.get("tipo") or c.get("descricao"),
            "situacao": c.get("situacao"),
            "observacao": c.get("observacao"),
        })
    return {"razao_social": d.get("razaoSocial"), "certidoes": certs}


def dados_cnpj(doc: Documento) -> dict:
    if doc.tipo != "CNPJ":
        return {}
    for url in (f"https://brasilapi.com.br/api/cnpj/v1/{doc.valor}", f"https://minhareceita.org/{doc.valor}"):
        try:
            d = requisitar("GET", url, cache_horas=168, tentativas=2)
        except ErroFonte as e:
            log.warning("CNPJ (%s): %s", url, e)
            continue
        if d:
            return {
                "razao_social": d.get("razao_social"),
                "nome_fantasia": d.get("nome_fantasia"),
                "situacao_cadastral": d.get("descricao_situacao_cadastral"),
                "abertura": d.get("data_inicio_atividade"),
                "municipio_uf": f"{d.get('municipio')}/{d.get('uf')}" if d.get("municipio") else None,
                "atividade_principal": d.get("cnae_fiscal_descricao"),
                "capital_social": d.get("capital_social"),
                "socios": [f"{s.get('nome_socio')} ({s.get('qualificacao_socio')}" + (f", desde {s['data_entrada_sociedade']}" if s.get('data_entrada_sociedade') else "") + ")"
                           for s in d.get("qsa") or []],
                "_socios": [{"nome": s.get("nome_socio"),
                             "documento": (s.get("cnpj_cpf_do_socio") or "").replace(".", "").replace("-", "").replace("/", ""),
                             "tipo": "CNPJ" if str(s.get("identificador_de_socio")) == "1" else "CPF",
                             "qualificacao": s.get("qualificacao_socio")}
                            for s in d.get("qsa") or [] if s.get("nome_socio")],
            }
    return {}
