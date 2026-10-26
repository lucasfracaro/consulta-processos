"""Catálogo de fontes: o que cada uma traz, se precisa de chave e como testá-la.

Tipos de papel na consulta:
  identidade  descobre nome, sócios e vínculos a partir do documento
  descoberta  encontra processos a partir do documento (ou do nome)
  detalhe     completa processos já encontrados, pelo número
  restricoes  sanções, certidões e dívidas
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .. import config as cfgmod
from ..documento import Documento
from ..http import requisitar

DOC_TESTE = Documento("CNPJ", "00000000000191", "00.000.000/0001-91")   # Banco do Brasil (público)


@dataclass
class InfoFonte:
    id: str
    nome: str
    categoria: str                 # "Oficial gratuita" | "Paga" | "Personalizada"
    papel: str
    descricao: str
    traz: list[str]
    precisa_chave: bool = False
    chave_opcional: bool = False
    link_chave: str | None = None
    ativa_por_padrao: bool = True
    campos_extra: list[dict] = field(default_factory=list)   # [{"id": "pasta", "rotulo": "...", "dica": "..."}]
    observacao: str | None = None


CATALOGO: list[InfoFonte] = [
    InfoFonte("receita", "Receita Federal — dados abertos do CNPJ", "Oficial gratuita", "identidade",
              "Razão social, nome fantasia, situação cadastral e quadro de sócios (via espelhos públicos BrasilAPI/minhareceita).",
              ["Razão social", "Sócios PF e PJ", "Situação cadastral", "CNAE e capital"]),
    InfoFonte("transparencia", "Portal da Transparência (CGU)", "Oficial gratuita", "identidade",
              "Nome a partir do CPF, vínculos em bases federais e sanções CEIS/CNEP.",
              ["Nome pelo CPF", "Vínculos federais", "Sanções CEIS/CNEP"], precisa_chave=True,
              link_chave="https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email",
              observacao="Chave gratuita, enviada por e-mail após o cadastro."),
    InfoFonte("djen", "DJEN — Diário de Justiça Eletrônico Nacional (CNJ)", "Oficial gratuita", "descoberta",
              "Intimações e editais de todos os tribunais; é a principal forma gratuita de achar processos.",
              ["Números dos processos", "Partes e polos", "Advogados", "Texto integral das intimações"]),
    InfoFonte("datajud", "DataJud — API Pública (CNJ)", "Oficial gratuita", "detalhe",
              "Capa oficial e todos os andamentos de cada processo encontrado.",
              ["Classe e assuntos", "Órgão julgador", "Data de ajuizamento", "Andamentos completos"],
              precisa_chave=False, chave_opcional=True, link_chave="https://datajud-wiki.cnj.jus.br/api-publica/acesso",
              observacao="Usa a chave pública do CNJ automaticamente; preencha só se o CNJ trocar a chave."),
    InfoFonte("tcu", "TCU — certidões consolidadas", "Oficial gratuita", "restricoes",
              "Inidôneos do TCU, improbidade (CNIA/CNJ), CEIS e CNEP. Somente CNPJ.",
              ["Licitantes inidôneos", "Improbidade administrativa"]),
    InfoFonte("pgfn", "PGFN — Dívida ativa da União (dados abertos)", "Oficial gratuita", "restricoes",
              "Inscrições em dívida ativa (geral, previdenciária e FGTS), valores e se já viraram execução fiscal.",
              ["Inscrições", "Valores", "Ajuizamento"], ativa_por_padrao=False,
              link_chave="https://www.gov.br/pgfn/pt-br/assuntos/divida-ativa-da-uniao/transparencia-fiscal-1/dados-abertos",
              campos_extra=[{"id": "pasta", "rotulo": "Pasta com os arquivos ZIP baixados da PGFN",
                             "dica": "/home/usuario/dados_pgfn"}],
              observacao="Não tem API: baixe os arquivos trimestrais e informe a pasta."),
    InfoFonte("escavador", "Escavador", "Paga", "descoberta",
              "Busca processos diretamente pelo CPF/CNPJ nos tribunais e diários; cobra por consulta.",
              ["Processos pelo documento", "Partes com CPF/CNPJ", "Valor da causa", "Status"],
              precisa_chave=True, link_chave="https://api.escavador.com/", ativa_por_padrao=False),
    InfoFonte("judit", "Judit", "Paga", "descoberta",
              "Busca assíncrona por CPF/CNPJ em todos os tribunais; cobra por requisição.",
              ["Processos pelo documento", "Partes", "Andamentos", "Valor da causa"],
              precisa_chave=True, link_chave="https://judit.io/", ativa_por_padrao=False),
]

# Provedores conhecidos que podem ser ligados pelo assistente (sem conector próprio).
SUGESTOES = [
    {"nome": "Codilo", "site": "https://www.codilo.com.br/"},
    {"nome": "Digesto", "site": "https://www.digesto.com.br/"},
    {"nome": "Infosimples", "site": "https://infosimples.com/"},
    {"nome": "BigDataCorp", "site": "https://bigdatacorp.com.br/"},
    {"nome": "Direct Data", "site": "https://www.directd.com.br/"},
]

POR_ID = {f.id: f for f in CATALOGO}


def estado(cfg: dict, fid: str) -> dict:
    """Situação de uma fonte: ativa?, tem chave?, pronta para uso?"""
    info = POR_ID.get(fid)
    c = cfg["fontes"].get(fid, {})
    if info is None:  # personalizada
        defin = next((d for d in cfg["personalizadas"] if d["id"] == fid), {})
        ativa = c.get("ativa", True)
        tem_chave = bool(c.get("chave"))
        return {"ativa": ativa, "tem_chave": tem_chave, "pronta": ativa and bool(defin)}
    ativa = c.get("ativa", info.ativa_por_padrao)
    tem_chave = bool(c.get("chave"))
    extra_ok = all((c.get("extra") or {}).get(x["id"]) for x in info.campos_extra)
    pronta = ativa and (tem_chave or not info.precisa_chave) and extra_ok
    return {"ativa": ativa, "tem_chave": tem_chave, "pronta": pronta}


def listar(cfg: dict) -> dict:
    fontes = []
    for info in CATALOGO:
        c = cfg["fontes"].get(info.id, {})
        fontes.append({**asdict(info), **estado(cfg, info.id), "chave_mascarada": cfgmod.mascarar(c.get("chave")),
                       "extra": c.get("extra") or {}, "teste": c.get("teste")})
    for d in cfg["personalizadas"]:
        c = cfg["fontes"].get(d["id"], {})
        fontes.append({"id": d["id"], "nome": d["nome"], "categoria": "Personalizada", "papel": "descoberta",
                       "descricao": f"{d.get('metodo', 'GET')} {d['url']}", "traz": d.get("campos_reconhecidos", []),
                       "precisa_chave": bool(c.get("chave")), "chave_opcional": True, "link_chave": d.get("site"),
                       "campos_extra": [], "observacao": "Adicionada pelo assistente; campos mapeados automaticamente.",
                       **estado(cfg, d["id"]), "chave_mascarada": cfgmod.mascarar(c.get("chave")),
                       "extra": {}, "teste": c.get("teste")})
    return {"fontes": fontes, "sugestoes": SUGESTOES}


def chave(cfg: dict, fid: str) -> str | None:
    return (cfg["fontes"].get(fid) or {}).get("chave") or None


def extra(cfg: dict, fid: str, campo: str) -> str | None:
    return ((cfg["fontes"].get(fid) or {}).get("extra") or {}).get(campo) or None


def pronta(cfg: dict, fid: str) -> bool:
    return estado(cfg, fid)["pronta"]


def buscadores_pagos_e_personalizados(cfg: dict) -> list:
    """Fontes que acham processos pelo documento (além do DJEN)."""
    from .escavador import Escavador
    from .generica import FonteGenerica
    from .judit import Judit
    saida = []
    if pronta(cfg, "escavador"):
        saida.append(Escavador(chave(cfg, "escavador")))
    if pronta(cfg, "judit"):
        saida.append(Judit(chave(cfg, "judit")))
    for d in cfg["personalizadas"]:
        if pronta(cfg, d["id"]):
            saida.append(FonteGenerica(d, chave(cfg, d["id"])))
    return saida


def testar(cfg: dict, fid: str) -> str:
    """Faz uma chamada simples para verificar se a fonte responde e a chave é aceita."""
    import os
    from pathlib import Path
    k = chave(cfg, fid)
    if fid == "receita":
        d = requisitar("GET", "https://brasilapi.com.br/api/cnpj/v1/00000000000191", tentativas=2)
        return f"Respondeu: {d.get('razao_social', 'ok')}"
    if fid == "transparencia":
        if not k:
            return "Informe a chave para testar."
        requisitar("GET", "https://api.portaldatransparencia.gov.br/api-de-dados/ceis",
                   headers={"chave-api-dados": k}, params={"codigoSancionado": "00000000000191", "pagina": 1}, tentativas=1)
        return "Chave aceita pelo Portal da Transparência."
    if fid == "djen":
        requisitar("GET", "https://comunicaapi.pje.jus.br/api/v1/comunicacao",
                   params={"numeroProcesso": "00008323520184013202", "pagina": 1, "itensPorPagina": 1}, tentativas=2)
        return "DJEN respondeu."
    if fid == "datajud":
        from .datajud import DataJud
        DataJud(k).consultar("00008323520184013202")
        return "DataJud respondeu" + (" com a chave informada." if k else " com a chave pública.")
    if fid == "tcu":
        requisitar("GET", "https://certidoes-apf.apps.tcu.gov.br/api/rest/publico/certidoes/00000000000191",
                   params={"seEmitirPDF": "false"}, tentativas=2)
        return "TCU respondeu."
    if fid == "pgfn":
        pasta = extra(cfg, "pgfn", "pasta")
        if not pasta or not Path(os.path.expanduser(pasta)).is_dir():
            return "Pasta não encontrada."
        n = sum(1 for a in Path(os.path.expanduser(pasta)).rglob("*") if a.suffix.lower() in (".zip", ".csv"))
        return f"{n} arquivo(s) ZIP/CSV encontrados na pasta."
    if fid == "escavador":
        from .escavador import Escavador
        return Escavador(k).testar() if k else "Informe a chave para testar."
    if fid == "judit":
        from .judit import Judit
        return Judit(k).testar() if k else "Informe a chave para testar."
    defin = next((d for d in cfg["personalizadas"] if d["id"] == fid), None)
    if defin:
        from .generica import FonteGenerica
        return FonteGenerica(defin, k).testar(DOC_TESTE)
    return "Fonte desconhecida."
