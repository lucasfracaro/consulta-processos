"""Interpretação de cada processo: ramo da Justiça, natureza, fase, situação do andamento,
resultado e alertas de risco.

Usa a classe e os assuntos (Tabelas Processuais Unificadas do CNJ), as movimentações do DataJud
e o texto das publicações do DJEN. São regras por palavras-chave, porque nomes de movimentos e
classes variam entre tribunais. O relatório mostra o movimento que embasou cada conclusão.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from .documento import cnj_digitos
from .modelo import Processo

RAMOS = {"1": "STF", "2": "CNJ", "3": "STJ", "4": "Justiça Federal", "5": "Justiça do Trabalho",
         "6": "Justiça Eleitoral", "7": "Justiça Militar da União", "8": "Justiça Estadual",
         "9": "Justiça Militar Estadual"}

# (natureza, palavras na classe/assuntos) — ordem importa: a primeira que casar vence.
NATUREZAS = [
    ("Criminal", ["penal", "crime", "criminal", "inquerito", "termo circunstanciado", "flagrante",
                  "medidas protetivas", "execucao da pena", "habeas corpus", "contravenc"]),
    ("Falência / Recuperação", ["falencia", "recuperacao judicial", "recuperacao extrajudicial", "insolvencia"]),
    ("Improbidade administrativa", ["improbidade"]),
    ("Execução fiscal / Tributário", ["execucao fiscal", "tributari", "divida ativa", "icms", "iptu", "iss "]),
    ("Trabalhista", ["trabalh", "reclamacao trabalhista", "verbas rescisorias", "horas extras"]),
    ("Previdenciário", ["previdenci", "aposentadoria", "auxilio-doenca", "auxilio doenca", "beneficio assistencial"]),
    ("Família / Sucessões", ["familia", "alimentos", "divorcio", "guarda", "inventario", "arrolamento", "uniao estavel"]),
    ("Consumidor", ["consumidor"]),
    ("Execução / Cobrança de dívida", ["execucao de titulo", "monitoria", "cobranca", "busca e apreensao",
                                      "cumprimento de sentenca", "duplicata", "cheque", "nota promissoria"]),
    ("Ambiental", ["ambiental", "meio ambiente"]),
    ("Administrativo / Público", ["administrativ", "licitac", "servidor publico", "mandado de seguranca"]),
    ("Eleitoral", ["eleitoral"]),
    ("Cível – indenização / contratos", ["indenizacao", "dano moral", "dano material", "responsabilidade civil",
                                         "contrato", "obrigacao de fazer", "despejo", "locacao", "posse", "usucapiao"]),
]

ALERTAS = {
    "Criminal": "Processo criminal",
    "Falência / Recuperação": "Falência ou recuperação judicial",
    "Improbidade administrativa": "Improbidade administrativa",
    "Execução fiscal / Tributário": "Dívida fiscal em execução",
}

# Eventos em ordem de verificação; (rótulo, padrões no nome do movimento/publicação)
EV_ARQUIVA = ["baixa definitiva", "arquivamento definitivo", "arquivado definitivamente", "arquivamento"]
EV_DESARQ = ["desarquivamento", "reativacao", "reativado"]
EV_SUSPENDE = ["suspensao", "sobrestamento", "sobrestado", "suspenso"]
EV_TRANSITO = ["transito em julgado", "transitado em julgado"]
EV_RESULTADO = [
    ("Acordo homologado", ["homologacao de transacao", "homologado o acordo", "homologacao de acordo", "acordo"]),
    ("Procedente em parte", ["procedencia em parte", "procedente em parte", "parcialmente procedente"]),
    ("Improcedente", ["improcedencia", "improcedente"]),
    ("Procedente", ["procedencia", "procedente"]),
    ("Extinto sem resolução de mérito", ["extincao do processo sem resolucao", "sem resolucao do merito",
                                         "sem resolucao de merito", "desistencia", "abandono"]),
    ("Execução extinta / dívida quitada", ["extincao da execucao", "satisfacao da obrigacao", "pagamento"]),
    ("Prescrição", ["prescricao"]),
    ("Condenação", ["condenacao", "condenado"]),
    ("Absolvição", ["absolvicao", "absolvido"]),
]
EV_RECURSO = ["remessa ao tribunal", "recurso", "apelacao", "agravo", "embargos de declaracao"]
EV_SENTENCA = ["sentenca", "julgamento", "julgado"]
EV_PENHORA = ["penhora", "bloqueio", "bacenjud", "sisbajud", "renajud", "arresto", "constricao"]


def _n(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s)


def _tem(texto: str, padroes: list[str]) -> bool:
    return any(p in texto for p in padroes)


def analisar(p: Processo, hoje: date | None = None) -> None:
    hoje = hoje or date.today()
    d = cnj_digitos(p.numero_cnj)
    p.ramo = RAMOS.get(d[13]) if len(d) == 20 else None
    if not p.data_distribuicao and len(d) == 20 and 1990 <= int(d[9:13]) <= hoje.year:
        p.ano_ajuizamento = d[9:13]   # o número CNJ traz o ano de início (NNNNNNN-DD.AAAA.J.TR.OOOO)
    elif p.data_distribuicao:
        p.ano_ajuizamento = p.data_distribuicao[:4]
    if p.ramo and p.ramo != "Justiça Estadual" and not p.area:
        p.area = {"Justiça do Trabalho": "Trabalhista", "Justiça Eleitoral": "Eleitoral"}.get(p.ramo, p.area)

    # ---- natureza
    base = _n(" ".join([p.classe or ""] + p.assuntos))
    p.natureza = next((nome for nome, pads in NATUREZAS if _tem(base, pads)), None)
    if not p.natureza and p.ramo == "Justiça do Trabalho":
        p.natureza = "Trabalhista"
    if not p.natureza and p.publicacoes:  # sem capa: tenta pelo texto da intimação
        txt = _n(" ".join((pb.get("texto") or "")[:1500] for pb in p.publicacoes[:5]))
        p.natureza = next((nome for nome, pads in NATUREZAS if _tem(txt, pads)), None)
    p.natureza = p.natureza or "Cível – outros"

    # ---- tipo de procedimento
    cl = _n(p.classe)
    if _tem(cl, ["execucao", "cumprimento de sentenca", "execucao fiscal"]):
        p.fase = "Execução / cobrança"
    elif _tem(cl, ["recurso", "apelacao", "agravo", "embargos"]) or (p.grau or "").upper() in ("G2", "SEGUNDO GRAU", "TR"):
        p.fase = "Recursal"
    elif _tem(cl, ["cautelar", "tutela", "liminar"]):
        p.fase = "Cautelar / urgência"
    elif cl:
        p.fase = "Conhecimento (discussão do mérito)"

    # ---- linha do tempo: movimentos (cronológico) + publicações
    eventos = sorted(
        [(m.data or "", _n(f"{m.descricao} {m.complemento or ''}"), m.descricao) for m in p.movimentacoes],
        key=lambda e: e[0])
    arquivado = suspenso = transitou = recurso = penhora = sentenca = False
    resultado = base_status = None
    for data, txt, original in eventos:
        if _tem(txt, EV_DESARQ):
            arquivado = False; base_status = (data, original)
        elif _tem(txt, EV_ARQUIVA):
            arquivado = True; base_status = (data, original)
        if _tem(txt, EV_SUSPENDE) and "levantamento" not in txt:
            suspenso = True; base_status = (data, original)
        elif suspenso and not _tem(txt, ["publicacao", "intimacao", "expedicao"]):
            suspenso = False
        if _tem(txt, EV_TRANSITO):
            transitou = True
        if _tem(txt, EV_RECURSO):
            recurso = True
        if _tem(txt, EV_PENHORA):
            penhora = True
        if _tem(txt, EV_SENTENCA):
            sentenca = True
        for rotulo, pads in EV_RESULTADO:
            if _tem(txt, pads):
                resultado = (rotulo, data, original)
                break

    dias_parado = None
    if p.data_ultima_movimentacao:
        try:
            dias_parado = (hoje - date.fromisoformat(p.data_ultima_movimentacao[:10])).days
        except ValueError:
            pass

    if arquivado:
        status = "Arquivado / baixado"
    elif suspenso:
        status = "Suspenso / sobrestado"
    elif dias_parado is not None and dias_parado > 365:
        status = f"Sem movimentação há {dias_parado // 30} meses"
    elif p.movimentacoes or p.publicacoes:
        status = "Em andamento"
    else:
        status = "Sem informação de andamento"
    if transitou and not arquivado:
        status += " (após trânsito em julgado)"
    p.status_andamento = status
    p.situacao = "INATIVO" if arquivado else "ATIVO"
    if resultado:
        p.resultado = f"{resultado[0]} — {resultado[2]} em {resultado[1][:10]}"
    elif sentenca:
        p.resultado = "Houve sentença/julgamento (resultado não identificado automaticamente)"
    if base_status:
        p.informacoes_extras["Base do status"] = f"{base_status[1]} em {base_status[0][:10]}"
    p.marcos = [m for m, ok in [("Sentença/julgamento", sentenca), ("Recurso", recurso),
                                ("Trânsito em julgado", transitou), ("Penhora/bloqueio de bens", penhora)] if ok]

    # ---- alertas para o consultado
    alertas = []
    if p.natureza in ALERTAS and p.polo_do_consultado in ("PASSIVO", None):
        alertas.append(ALERTAS[p.natureza])
    if p.natureza == "Trabalhista" and p.polo_do_consultado == "PASSIVO":
        alertas.append("Réu em ação trabalhista")
    if penhora and p.polo_do_consultado == "PASSIVO" and not arquivado:
        alertas.append("Constrição de bens (penhora/bloqueio)")
    if p.fase == "Execução / cobrança" and p.polo_do_consultado == "PASSIVO" and not arquivado:
        alertas.append("Executado em cobrança ativa")
    p.alertas = alertas
