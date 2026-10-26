"""Tradução de cada processo para português simples: o que é o tipo de ação, quem está contra
quem, do que se trata, em que pé está e o trecho principal do último ato publicado."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from .modelo import Processo

# (trecho da classe normalizado, nome amigável, explicação)
CLASSES = [
    ("execucao fiscal", "Cobrança de dívida com o governo",
     "O governo (União, Estado, Município ou autarquia) cobra na Justiça tributos, multas ou taxas não pagos e já inscritos em dívida ativa. O devedor pode ter bens penhorados."),
    ("embargos a execucao fiscal", "Defesa contra cobrança do governo",
     "O devedor contesta uma execução fiscal: alega que a dívida é indevida, prescrita ou com valor errado."),
    ("embargos a execucao", "Defesa contra cobrança",
     "O devedor contesta uma execução (cobrança) movida contra ele."),
    ("embargos de terceiro", "Defesa de bem atingido em processo alheio",
     "Alguém que não é parte pede a liberação de um bem penhorado ou apreendido num processo de outra pessoa."),
    ("execucao de titulo extrajudicial", "Cobrança de dívida documentada",
     "Cobrança direta de dívida baseada em documento com força de título (contrato assinado, cheque, nota promissória, duplicata, confissão de dívida). O devedor é chamado a pagar e pode ter bens penhorados."),
    ("cumprimento de sentenca", "Cobrança do que foi decidido",
     "Fase em que se executa o que a sentença determinou: pagamento de valores, entrega de bem ou obrigação de fazer. Pode haver bloqueio de contas e penhora."),
    ("execucao de titulo judicial", "Cobrança do que foi decidido",
     "Execução do que foi decidido em processo anterior."),
    ("monitoria", "Cobrança de dívida sem título executivo",
     "O credor tem uma prova escrita da dívida (por exemplo, nota fiscal ou e-mail), mas sem força de título; pede que o juiz ordene o pagamento."),
    ("busca e apreensao", "Retomada de bem financiado",
     "Normalmente o banco ou financeira pede a apreensão de um bem (veículo, máquina) financiado com alienação fiduciária por falta de pagamento."),
    ("despejo", "Despejo",
     "O locador pede a retirada do inquilino do imóvel, em geral por falta de pagamento do aluguel ou fim do contrato."),
    ("reintegracao", "Retomada de posse",
     "Quem perdeu a posse de um imóvel ou bem pede para recuperá-la."),
    ("usucapiao", "Usucapião",
     "Pedido para obter a propriedade de um imóvel pela posse prolongada."),
    ("recuperacao judicial", "Recuperação judicial",
     "Empresa em crise pede proteção para renegociar dívidas com os credores sob supervisão da Justiça, evitando a falência."),
    ("falencia", "Falência",
     "Pedido para decretar a falência de uma empresa que não paga suas dívidas; os bens são vendidos para pagar credores."),
    ("acao civil publica", "Ação civil pública",
     "Ação movida em geral pelo Ministério Público, Defensoria ou associação para proteger interesses coletivos (consumidores, meio ambiente, patrimônio público)."),
    ("improbidade", "Improbidade administrativa",
     "Acusação de ato ilegal contra a administração pública (desvio, enriquecimento ilícito, fraude em licitação). Pode gerar multa, ressarcimento e proibição de contratar com o poder público."),
    ("mandado de seguranca", "Mandado de segurança",
     "Ação rápida contra ato ilegal de autoridade pública (por exemplo, autuação fiscal, negativa de licença)."),
    ("acao penal", "Processo criminal",
     "O Ministério Público (ou a vítima) acusa alguém de crime. Pode resultar em condenação ou absolvição."),
    ("inquerito", "Investigação criminal",
     "Investigação conduzida pela polícia para apurar um possível crime; ainda não é acusação formal."),
    ("termo circunstanciado", "Infração de menor potencial ofensivo",
     "Registro de infração penal leve (pena máxima até 2 anos), tratada no Juizado Especial Criminal."),
    ("habeas corpus", "Habeas corpus",
     "Pedido para proteger a liberdade de locomoção de alguém contra prisão ou ameaça ilegal."),
    ("medidas protetivas", "Medidas protetivas",
     "Medidas de proteção, em geral no contexto da Lei Maria da Penha."),
    ("execucao da pena", "Execução penal",
     "Acompanhamento do cumprimento de uma pena criminal já aplicada."),
    ("acao trabalhista", "Reclamação trabalhista",
     "Empregado ou ex-empregado cobra direitos trabalhistas (verbas rescisórias, horas extras, FGTS, indenizações) do empregador."),
    ("reclamacao trabalhista", "Reclamação trabalhista",
     "Empregado ou ex-empregado cobra direitos trabalhistas do empregador."),
    ("recurso ordinario trabalhista", "Recurso trabalhista",
     "Recurso contra sentença da Justiça do Trabalho, julgado pelo TRT."),
    ("agravo de peticao", "Recurso na cobrança trabalhista",
     "Recurso contra decisões tomadas na fase de execução de um processo trabalhista."),
    ("juizado especial", "Ação de menor valor (Juizado Especial)",
     "Causa de valor menor (até 40 salários mínimos no Estadual, 60 no Federal), com rito mais rápido e simples."),
    ("recurso inominado", "Recurso no Juizado Especial",
     "Recurso contra sentença do Juizado Especial, julgado pela Turma Recursal."),
    ("apelacao", "Recurso contra sentença",
     "A parte que perdeu pede ao tribunal (2º grau) que reveja a sentença."),
    ("agravo de instrumento", "Recurso contra decisão no meio do processo",
     "Recurso ao tribunal contra uma decisão tomada durante o processo (por exemplo, liminar concedida ou negada)."),
    ("agravo", "Recurso",
     "Recurso contra uma decisão judicial."),
    ("embargos de declaracao", "Pedido de esclarecimento",
     "Pedido ao próprio juiz para esclarecer omissão, contradição ou obscuridade numa decisão."),
    ("recurso especial", "Recurso ao STJ",
     "Recurso ao Superior Tribunal de Justiça alegando violação de lei federal."),
    ("recurso extraordinario", "Recurso ao STF",
     "Recurso ao Supremo Tribunal Federal alegando violação da Constituição."),
    ("carta precatoria", "Ato pedido a outra comarca",
     "Um juiz pede a outro, de outra cidade, que pratique um ato (citar alguém, ouvir testemunha, penhorar bem)."),
    ("divorcio", "Divórcio", "Pedido de dissolução do casamento, com partilha de bens, guarda e pensão quando houver."),
    ("alimentos", "Pensão alimentícia", "Pedido, revisão ou cobrança de pensão alimentícia."),
    ("guarda", "Guarda de filhos", "Definição de com quem ficam os filhos e regras de convivência."),
    ("inventario", "Inventário", "Partilha dos bens de pessoa falecida entre os herdeiros."),
    ("arrolamento", "Inventário simplificado", "Partilha simplificada dos bens de pessoa falecida."),
    ("consignacao em pagamento", "Depósito judicial de pagamento",
     "O devedor deposita em juízo o valor que entende devido, quando o credor se recusa a receber ou há dúvida sobre a quem pagar."),
    ("tutela cautelar", "Pedido urgente", "Pedido urgente para preservar um direito antes ou durante o processo principal."),
    ("tutela antecipada", "Pedido urgente", "Pedido urgente para antecipar os efeitos do que se pede no processo."),
    ("procedimento comum", "Ação judicial comum",
     "Ação em que se pede ao juiz que reconheça um direito (indenização, cobrança, cumprimento de contrato, revisão de cláusulas etc.), com produção de provas e sentença."),
    ("procedimento sumario", "Ação judicial de rito sumário", "Ação cível de rito mais curto."),
]

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
FORTES = ["julgo", "condeno", "homologo", "defiro", "indefiro", "determino", "decreto", "declaro", "extingo",
          "rejeito", "acolho", "concedo", "indefere-se", "defere-se", "nego provimento", "dou provimento",
          "dou parcial provimento", "mantenho", "absolvo", "pronuncio"]
FRACOS = ["recebo", "suspendo", "arquive", "intime-se", "intimem-se", "cite-se", "penhora", "bloqueio",
          "designo audiencia", "vista ao", "manifeste-se"]


def _n(s: str | None) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _mes_ano(d: str | None) -> str | None:
    if not d or len(d) < 7 or not d[:4].isdigit():
        return None
    return f"{MESES[int(d[5:7]) - 1]}/{d[:4]}" if d[5:7].isdigit() and 1 <= int(d[5:7]) <= 12 else d[:4]


def _data_br(d: str | None) -> str | None:
    if not d or len(d) < 10:
        return d
    return f"{d[8:10]}/{d[5:7]}/{d[:4]}"


def explicar_classe(classe: str | None) -> tuple[str | None, str | None]:
    c = _n(classe)
    for chave, amigavel, explic in CLASSES:
        if chave in c:
            return amigavel, explic
    return None, None


def trecho_decisao(texto: str, limite: int = 420) -> str:
    """Extrai o trecho mais informativo de uma publicação (onde o juiz decide/determina algo)."""
    t = re.sub(r"\s+", " ", texto or "").strip()
    if not t:
        return ""
    # versão sem acentos e minúscula com o MESMO tamanho do original (posições batem)
    tn = "".join((unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode() or " ")[0].lower() for c in t)
    fortes = [tn.rfind(v) for v in FORTES if v in tn]
    fracos = [tn.find(v) for v in FRACOS if v in tn]
    # prefere o dispositivo (onde o juiz julga/decide), normalmente no fim do texto
    pos = max(fortes) if fortes else (min(fracos) if fracos else 0)
    ini = max(0, t.rfind(". ", 0, pos) + 2) if pos else 0
    if pos and pos - ini > 300:   # frase muito longa antes do verbo: começa um pouco antes dele
        ini = max(0, t.rfind(" ", 0, pos - 150) + 1)
    trecho = t[ini:ini + limite].strip()
    return ("…" if ini else "") + trecho + ("…" if ini + limite < len(t) else "")


def explicar(p: Processo, nome_alvo: str | None) -> None:
    hoje = date.today()
    amigavel, p.explicacao_classe = explicar_classe(p.classe)
    tipo = amigavel or p.classe or p.natureza or "processo"

    # parte contrária
    meu_polo = p.polo_do_consultado
    oposto = {"ATIVO": "PASSIVO", "PASSIVO": "ATIVO"}.get(meu_polo)
    alvo_n = _n(nome_alvo)
    contrarios = [pt.nome for pt in p.partes if pt.nome and pt.polo == oposto
                  and not (alvo_n and _n(pt.nome)[:20] == alvo_n[:20])]
    p.parte_contraria = list(dict.fromkeys(contrarios))[:5]

    # último ato publicado
    if p.publicacoes:
        ult = max(p.publicacoes, key=lambda pb: pb.get("data") or "")
        p.ultimo_ato = {"data": ult.get("data"), "tipo": ult.get("tipo") or ult.get("documento"),
                        "trecho": trecho_decisao(ult.get("texto") or "")}
    elif p.movimentacoes:
        m = max(p.movimentacoes, key=lambda m: m.data or "")
        p.ultimo_ato = {"data": m.data, "tipo": "Andamento", "trecho": m.descricao}

    # frase-resumo
    quem = nome_alvo or "O investigado"
    lado = {"ATIVO": "autor", "PASSIVO": "réu", "TERCEIRO": "terceiro interessado"}.get(meu_polo)
    papel = (p.papel_do_consultado or "").lower()
    if lado and papel and papel not in (lado, "autora", "ré", "re", "reu"):
        lado = f"{lado} ({papel})"
    frase = f"{quem} é {lado} neste processo" if lado else f"{quem} aparece neste processo"
    frase += f" do tipo “{tipo[0].lower() + tipo[1:]}”"
    if p.parte_contraria:
        contra = "contra" if meu_polo == "ATIVO" else "movido por"
        frase += f", {contra} {', '.join(p.parte_contraria[:2])}{' e outros' if len(p.parte_contraria) > 2 else ''}"
    onde = " – ".join(x for x in (p.ramo, p.tribunal) if x)
    if onde:
        frase += f", na {onde}"
    if p.orgao_julgador:
        frase += f" ({p.orgao_julgador})"
    frase += "."
    assunto = (p.assuntos[0].split(">")[-1].strip() if p.assuntos else None)
    if assunto:
        frase += f" Trata de: {assunto}."
    inicio = _mes_ano(p.data_distribuicao) or p.ano_ajuizamento
    if inicio:
        anos = hoje.year - int((p.data_distribuicao or p.ano_ajuizamento)[:4])
        frase += f" Começou em {inicio}" + (f" (há {anos} anos)" if anos >= 2 else "") + "."
    if p.status_andamento:
        frase += f" Situação: {p.status_andamento.lower()}."
    if p.resultado and not p.resultado.startswith("Houve"):
        frase += f" Resultado: {p.resultado.split(' — ')[0].lower()}."
    ultima = max(filter(None, [p.data_ultima_movimentacao, p.ultimo_ato.get("data")]), default=None)
    if ultima:
        frase += f" Última movimentação em {_data_br(ultima)}."
    p.resumo_simples = frase


def resumo_executivo(processos: list[Processo], nome: str | None) -> str:
    if not processos:
        return f"Não foram encontrados processos para {nome or 'o documento consultado'} nas fontes oficiais abertas."
    n = len(processos)
    reu = sum(1 for p in processos if p.polo_do_consultado == "PASSIVO")
    autor = sum(1 for p in processos if p.polo_do_consultado == "ATIVO")
    andamento = sum(1 for p in processos if (p.status_andamento or "").startswith(("Em andamento", "Suspenso")))
    temas: dict[str, int] = {}
    for p in processos:
        temas[p.natureza or "Outros"] = temas.get(p.natureza or "Outros", 0) + 1
    principais = ", ".join(f"{k.lower()} ({v})" for k, v in sorted(temas.items(), key=lambda x: -x[1])[:4])
    txt = (f"Foram localizados {n} processo{'s' if n > 1 else ''} envolvendo {nome or 'o consultado'} "
           f"e o seu grupo: em {reu} figura como réu/requerido e em {autor} como autor. "
           f"{andamento} ainda {'estão' if andamento != 1 else 'está'} ativo{'s' if andamento != 1 else ''} ou suspenso{'s' if andamento != 1 else ''}. "
           f"Temas principais: {principais}.")
    alert = sum(1 for p in processos if p.alertas)
    if alert:
        txt += f" {alert} processo{'s têm' if alert > 1 else ' tem'} pontos de atenção (veja abaixo)."
    return txt
