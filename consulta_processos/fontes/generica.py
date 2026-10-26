"""Fonte genérica: integra uma API nova sem escrever código para ela.

Duas partes:

1. ADAPTADOR — executa uma "definição" (endereço, autenticação, onde vai o CPF/CNPJ) e lê QUALQUER
   resposta JSON: percorre a estrutura, reconhece como processo todo objeto que tenha um número no
   padrão CNJ e mapeia os campos pelo nome das chaves (tribunal/court, classe/class, partes/parties,
   movimentacoes/steps, valor/amount, etc.), em português ou inglês. Segue paginação automaticamente.

2. ASSISTENTE (analisar) — recebe o endereço da API (ou da documentação OpenAPI/Swagger) e a chave;
   procura a especificação, escolhe as operações que aceitam CPF/CNPJ, descobre a forma de
   autenticação, testa com um documento real e devolve a definição pronta + uma amostra do que foi
   encontrado. Se a API não publicar especificação, aceita um endereço de busca com {documento}.

Limite: APIs assíncronas (pede-se a busca e busca-se o resultado depois) ou que exigem fluxo de login
precisam de um conector próprio, como os de Escavador e Judit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from urllib.parse import urljoin, urlparse

from ..documento import Documento, cnj_valido, so_digitos
from ..http import ErroFonte, requisitar
from ..modelo import Advogado, Movimentacao, Parte, Processo

log = logging.getLogger("consulta")
RE_CNJ = re.compile(r"^\s*\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}\s*$")
PARAM_DOC = re.compile(r"^(cpf|cnpj|cpf_?cnpj|cpfcnpj|documento|document|doc|nr_?documento|numero_?documento|"
                       r"tax_?id|search_?key|identificador)$", re.I)
PATH_BOM = re.compile(r"process|lawsuit|litig|envolvid|parte|party|acao|case", re.I)

SINONIMOS = {
    "tribunal": ["tribunal", "siglatribunal", "tribunalsigla", "tribunalacronym", "court", "courtacronym", "tribunalnome"],
    "classe": ["classe", "classeprocessual", "class", "classification", "classifications", "tipoacao", "nomeclasse"],
    "assuntos": ["assunto", "assuntos", "subject", "subjects", "assuntoprincipal", "materia"],
    "orgao": ["orgaojulgador", "orgao", "vara", "juizo", "courtname", "courts", "unidade", "comarca"],
    "distribuicao": ["datadistribuicao", "distribuicao", "dataajuizamento", "ajuizamento", "datainicio",
                     "distributiondate", "filingdate", "startdate", "datadeinicio"],
    "ultima": ["dataultimamovimentacao", "ultimamovimentacao", "lastupdate", "lastmovement", "updatedat", "datadaultimamovimentacao"],
    "valor": ["valorcausa", "valordacausa", "valor", "amount", "value", "claimvalue"],
    "situacao": ["situacao", "status", "statusprocesso", "situation", "phase", "fase"],
    "grau": ["grau", "instancia", "instance", "degree"],
    "segredo": ["segredojustica", "segredo", "sigilo", "secrecy", "secrecylevel", "nivelsigilo"],
    "partes": ["partes", "envolvidos", "parties", "participants", "polos", "litigantes", "people"],
    "movs": ["movimentacoes", "movimentos", "andamentos", "steps", "movements", "events", "timeline", "historico"],
    "link": ["url", "link", "href"],
}
P_NOME = ["nome", "name", "razaosocial", "fullname"]
P_POLO = ["polo", "side", "lado", "pole"]
P_TIPO = ["tipo", "type", "papel", "role", "qualificacao", "persontype", "tiponormalizado"]
P_DOC = ["cpf", "cnpj", "documento", "document", "maindocument", "cpfcnpj", "doc", "taxid"]
P_ADV = ["advogados", "lawyers", "attorneys", "representantes"]
M_DATA = ["data", "date", "datahora", "stepdate", "datetime", "created", "dt"]
M_DESC = ["descricao", "conteudo", "content", "nome", "name", "texto", "text", "description", "titulo", "title"]
NEXT = ["next", "nextpage", "nextpageurl", "nexturl", "proxima", "proximapagina"]


def _k(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _achar(d: dict, nomes: list[str]):
    idx = {_k(k): k for k in d}
    for n in nomes:
        if n in idx and d[idx[n]] not in (None, "", [], {}):
            return d[idx[n]]
    return None


def _texto(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (str, int, float)):
        return str(v)
    if isinstance(v, dict):
        return _texto(_achar(v, ["nome", "name", "descricao", "description", "sigla", "acronym", "valor", "value"]))
    if isinstance(v, list):
        itens = [_texto(x) for x in v]
        return ", ".join(x for x in itens if x) or None
    return None


def _lista_textos(v) -> list[str]:
    if isinstance(v, list):
        return [t for t in (_texto(x) for x in v) if t]
    t = _texto(v)
    return [t] if t else []


def _data(v) -> str | None:
    t = _texto(v)
    if not t:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return m.group(0)
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", t)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    if re.fullmatch(r"\d{14}|\d{8}", t):
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    return None


def _valor(v) -> float | None:
    t = _texto(v)
    if not t:
        return None
    t = re.sub(r"[^\d,.-]", "", t)
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


POLO_ATIVO = ("ativ", "activ", "autor", "plaintiff", "claimant", "requerente", "exequente", "reclamante",
              "agravante", "apelante", "impetrante", "embargante", "recorrente", "credor")
POLO_PASSIVO = ("passiv", "reu", "re", "defendant", "respondent", "requerid", "executad", "reclamad", "agravad",
                "apelad", "impetrad", "embargad", "recorrid", "devedor", "acusad", "denunciad")


def _polo(v) -> str | None:
    t = _k(_texto(v) or "")
    if not t:
        return None
    if t in ("a", "at", "ativo", "active"):
        return "ATIVO"
    if t in ("p", "pa", "passivo", "passive"):
        return "PASSIVO"
    if t.startswith(("adv", "lawyer", "attorney")):
        return "ADVOGADO"
    if t == "re" or t.startswith(POLO_PASSIVO[:2] + POLO_PASSIVO[3:]):
        return "PASSIVO"
    if t.startswith(POLO_ATIVO):
        return "ATIVO"
    return "TERCEIRO"


def _numero_cnj(d: dict) -> str | None:
    """Um campo de primeiro nível do objeto com número CNJ válido."""
    preferidos = sorted(d.items(), key=lambda kv: 0 if re.search(r"cnj|numero|number|code|processo", _k(kv[0])) else 1)
    for _, v in preferidos:
        if isinstance(v, (str, int)) and RE_CNJ.match(str(v)):
            n = so_digitos(str(v))
            if cnj_valido(n):
                return n
    return None


def extrair_processos(dados, doc: Documento | None, rotulo: str, fonte_id: str) -> list[Processo]:
    """Percorre qualquer JSON e transforma em Processo todo objeto que tenha um número CNJ."""
    achados: list[Processo] = []

    def visitar(no, dentro_de_processo=False):
        if isinstance(no, list):
            for x in no:
                visitar(x, dentro_de_processo)
        elif isinstance(no, dict):
            numero = None if dentro_de_processo else _numero_cnj(no)
            if numero:
                achados.append(_mapear(no, numero, doc, rotulo, fonte_id))
                visitar(list(no.values()), True)  # objetos internos fazem parte deste processo
            else:
                visitar(list(no.values()), dentro_de_processo)

    visitar(dados)
    return achados


def _mapear(d: dict, numero: str, doc: Documento | None, rotulo: str, fonte_id: str) -> Processo:
    # campos podem estar no próprio objeto ou num sub-objeto "capa"/"cover"/"dados"
    planos = dict(d)
    for k, v in d.items():
        if isinstance(v, dict) and _k(k) in ("capa", "cover", "dados", "data", "detalhes", "details", "metadata"):
            planos = {**v, **planos}
    p = Processo(
        numero_cnj=numero, envolvido=rotulo, fontes=[fonte_id],
        tribunal=_texto(_achar(planos, SINONIMOS["tribunal"])),
        classe=_texto(_achar(planos, SINONIMOS["classe"])),
        assuntos=_lista_textos(_achar(planos, SINONIMOS["assuntos"])),
        orgao_julgador=_texto(_achar(planos, SINONIMOS["orgao"])),
        data_distribuicao=_data(_achar(planos, SINONIMOS["distribuicao"])),
        data_ultima_movimentacao=_data(_achar(planos, SINONIMOS["ultima"])),
        valor_causa=_valor(_achar(planos, SINONIMOS["valor"])),
        grau=_texto(_achar(planos, SINONIMOS["grau"])),
    )
    sit = _texto(_achar(planos, SINONIMOS["situacao"]))
    if sit:
        p.informacoes_extras[f"Situação ({fonte_id})"] = sit
    seg = _achar(planos, SINONIMOS["segredo"])
    if isinstance(seg, (bool, int)):
        p.segredo_justica = bool(seg)
    link = _texto(_achar(planos, SINONIMOS["link"]))
    if link and link.startswith("http"):
        p.links.append(link)
    alvo = doc.valor if doc else None
    for parte in _achar(planos, SINONIMOS["partes"]) or []:
        if not isinstance(parte, dict):
            continue
        documento = _texto(_achar(parte, P_DOC))
        pt = Parte(nome=_texto(_achar(parte, P_NOME)), polo=_polo(_achar(parte, P_POLO)),
                   tipo=_texto(_achar(parte, P_TIPO)), documento=documento,
                   advogados=[Advogado(_texto(_achar(a, P_NOME)) if isinstance(a, dict) else _texto(a))
                              for a in (_achar(parte, P_ADV) or []) if a])
        if not pt.polo and pt.tipo:
            pt.polo = _polo(pt.tipo)
        p.partes.append(pt)
        dg = re.sub(r"[^0-9A-Z]", "", (documento or "").upper())
        if alvo and dg and (dg == alvo or (doc.tipo == "CNPJ" and dg[:8] == alvo[:8])):
            p.polo_do_consultado, p.papel_do_consultado = pt.polo, pt.tipo
            p.como_foi_encontrado = "DOCUMENTO_NO_TEXTO"
    for m in _achar(planos, SINONIMOS["movs"]) or []:
        if isinstance(m, dict):
            p.movimentacoes.append(Movimentacao(data=_data(_achar(m, M_DATA)), descricao=_texto(_achar(m, M_DESC)),
                                                fonte=fonte_id))
    p.movimentacoes.sort(key=lambda m: m.data or "", reverse=True)
    if not p.data_ultima_movimentacao and p.movimentacoes:
        p.data_ultima_movimentacao = p.movimentacoes[0].data
    # a fonte foi consultada pelo documento: se não houver partes para conferir, a ligação vem da própria fonte
    p.como_foi_encontrado = p.como_foi_encontrado or "DOCUMENTO_NA_FONTE"
    return p


def _proxima(dados, url_atual: str) -> str | None:
    """Procura um link de próxima página em lugares comuns (links.next, next, next_page_url...)."""
    def busca(no, prof=0):
        if prof > 3 or not isinstance(no, dict):
            return None
        for k, v in no.items():
            if _k(k) in NEXT and isinstance(v, str) and v:
                return v
            if _k(k) in NEXT and isinstance(v, dict):
                href = _achar(v, ["href", "url"])
                if isinstance(href, str):
                    return href
        for k, v in no.items():
            if _k(k) in ("links", "link", "paging", "pagination", "paginacao", "meta", "_links"):
                r = busca(v, prof + 1)
                if r:
                    return r
        return None
    prox = busca(dados)
    return urljoin(url_atual, prox) if prox else None


# ---------------------------------------------------------------- execução de uma definição
def _auth(defin: dict, chave: str | None) -> tuple[dict, dict]:
    headers, params = {"Accept": "application/json"}, {}
    a = defin.get("auth") or {}
    if chave and a.get("tipo") == "bearer":
        headers["Authorization"] = f"Bearer {chave}"
    elif chave and a.get("tipo") == "header":
        headers[a.get("nome") or "api-key"] = f"{a.get('prefixo') or ''}{chave}"
    elif chave and a.get("tipo") == "query":
        params[a.get("nome") or "token"] = chave
    return headers, params


def _doc_valor(doc: Documento, formato: str) -> str:
    return doc.formatado if formato == "formatado" else doc.valor


class FonteGenerica:
    def __init__(self, definicao: dict, chave: str | None):
        self.defin, self.chave = definicao, chave
        self.id, self.nome = definicao["id"], definicao["nome"]

    def _url_e_params(self, doc: Documento) -> tuple[str, dict, dict | None]:
        d = self.defin
        valor = _doc_valor(doc, d.get("formato_documento", "digitos"))
        url = d["url"].replace("{documento}", valor).replace("{tipo}", doc.tipo.lower())
        params = {k: str(v).replace("{documento}", valor).replace("{tipo}", doc.tipo.lower())
                  for k, v in (d.get("params") or {}).items()}
        if d.get("param_documento"):
            params[d["param_documento"]] = valor
        corpo = None
        if d.get("corpo"):
            corpo = json.loads(json.dumps(d["corpo"]).replace("{documento}", valor).replace("{tipo}", doc.tipo.lower()))
        return url, params, corpo

    def buscar(self, doc: Documento, rotulo: str, max_paginas: int = 30) -> list[Processo]:
        if doc.tipo not in ("CPF", "CNPJ"):
            return []
        url, params, corpo = self._url_e_params(doc)
        headers, qauth = _auth(self.defin, self.chave)
        params.update(qauth)
        processos, vistos = [], set()
        for _ in range(max_paginas):
            dados = requisitar(self.defin.get("metodo", "GET"), url, headers=headers, params=params or None,
                               json_body=corpo, cache_horas=24)
            for p in extrair_processos(dados, doc, rotulo, self.nome):
                if p.numero_cnj not in vistos:
                    vistos.add(p.numero_cnj); processos.append(p)
            prox = _proxima(dados, url)
            if not prox or prox == url:
                break
            url, params, corpo = prox, dict(qauth) or None, None
        return processos

    def testar(self, doc: Documento | None = None) -> str:
        doc = doc or Documento("CNPJ", "00000000000191", "00.000.000/0001-91")
        achados = self.buscar(doc, "teste", max_paginas=1)
        return f"Conexão aceita; {len(achados)} processo(s) reconhecido(s) no teste."


# ---------------------------------------------------------------- assistente: análise automática de uma API nova
CANDIDATOS_SPEC = ["", "/openapi.json", "/swagger.json", "/v3/api-docs", "/api-docs", "/swagger/v1/swagger.json",
                   "/api/openapi.json", "/api/swagger.json", "/docs/openapi.json", "/api/v1/openapi.json"]
AUTH_TENTATIVAS = [{"tipo": "bearer"}, {"tipo": "header", "nome": "api-key"}, {"tipo": "header", "nome": "x-api-key"},
                   {"tipo": "header", "nome": "Authorization", "prefixo": "Token "}, {"tipo": "query", "nome": "token"},
                   {"tipo": "query", "nome": "api_key"}]


def _buscar_spec(endereco: str, passos: list[str]) -> tuple[dict | None, str | None]:
    base = endereco.rstrip("/")
    tentativas = [endereco] if endereco.lower().endswith((".json", "api-docs")) else [base + c for c in CANDIDATOS_SPEC]
    raiz = f"{urlparse(endereco).scheme}://{urlparse(endereco).netloc}"
    if raiz != base:
        tentativas += [raiz + c for c in CANDIDATOS_SPEC[1:]]
    for u in dict.fromkeys(tentativas):
        try:
            d = requisitar("GET", u, headers={"Accept": "application/json"}, tentativas=1, timeout=15)
        except ErroFonte:
            continue
        if isinstance(d, dict) and ("paths" in d and ("openapi" in d or "swagger" in d)):
            passos.append(f"Especificação encontrada em {u}")
            return d, u
    passos.append("Nenhuma especificação OpenAPI/Swagger encontrada nos endereços usuais.")
    return None, None


def _auth_da_spec(spec: dict) -> dict | None:
    esquemas = (spec.get("components") or {}).get("securitySchemes") or spec.get("securityDefinitions") or {}
    for s in esquemas.values():
        if s.get("type") == "http" and s.get("scheme", "").lower() == "bearer":
            return {"tipo": "bearer"}
        if s.get("type") == "apiKey":
            return {"tipo": "header" if s.get("in") == "header" else "query", "nome": s.get("name")}
    return None


def _operacoes(spec: dict, spec_url: str) -> list[dict]:
    servers = spec.get("servers") or []
    if servers:
        base = urljoin(spec_url, servers[0].get("url", "/"))
    else:
        base = f"{urlparse(spec_url).scheme}://{spec.get('host') or urlparse(spec_url).netloc}{spec.get('basePath', '')}"
    ops = []
    for caminho, metodos in (spec.get("paths") or {}).items():
        for metodo, op in metodos.items():
            if metodo.lower() not in ("get", "post") or not isinstance(op, dict):
                continue
            params = (op.get("parameters") or []) + (metodos.get("parameters") or [])
            nota, defin = 0, {"metodo": metodo.upper(), "url": base.rstrip("/") + caminho}
            for prm in params:
                nome = prm.get("name", "")
                if PARAM_DOC.match(nome):
                    nota += 10
                    if prm.get("in") == "path":
                        defin["url"] = defin["url"].replace("{" + nome + "}", "{documento}")
                    else:
                        defin["param_documento"] = nome
            corpo = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
            props = corpo.get("properties") or {}
            for nome in props:
                if PARAM_DOC.match(nome):
                    nota += 8
                    defin["corpo"] = {nome: "{documento}"}
            texto = f"{caminho} {op.get('summary', '')} {op.get('description', '')}"
            if PATH_BOM.search(texto):
                nota += 3
            if nota >= 8 and "{" not in defin["url"].replace("{documento}", ""):
                defin["_nota"] = nota
                defin["_descricao"] = f"{metodo.upper()} {caminho} — {op.get('summary') or ''}".strip(" —")
                ops.append(defin)
    return sorted(ops, key=lambda o: -o["_nota"])[:6]


def analisar(nome: str, endereco: str, chave: str | None, documento: Documento,
             auth: dict | None = None) -> dict:
    """Descobre como usar uma API nova e devolve {'definicao', 'amostra', 'passos'} ou levanta ErroFonte."""
    passos: list[str] = []
    candidatos: list[dict] = []
    if "{documento}" in endereco:
        candidatos = [{"metodo": "GET", "url": endereco}]
        passos.append("Usando o endereço de busca informado (com {documento}).")
    else:
        spec, spec_url = _buscar_spec(endereco, passos)
        if spec:
            auth = auth or _auth_da_spec(spec)
            if auth:
                passos.append(f"Autenticação indicada pela especificação: {auth}")
            candidatos = _operacoes(spec, spec_url)
            passos.append(f"{len(candidatos)} operação(ões) aceitam CPF/CNPJ: "
                          + "; ".join(c["_descricao"] for c in candidatos))
    if not candidatos:
        raise ErroFonte("Não encontrei como buscar por CPF/CNPJ nessa API. Informe o link da especificação "
                        "OpenAPI/Swagger ou um endereço de busca com {documento} no lugar do CPF/CNPJ "
                        "(ex.: https://api.exemplo.com/v1/processos?cpf_cnpj={documento}).")
    auths = [auth] if auth else (AUTH_TENTATIVAS if chave else [{}])
    ultimo_erro, sem_resultado = None, None
    for cand in candidatos:
        rotulo = cand.get("_descricao", cand["url"])
        for a in auths:
            auth_recusada = False
            for formato in ("digitos", "formatado"):
                defin = {k: v for k, v in cand.items() if not k.startswith("_")}
                defin.update(nome=nome, auth=a, formato_documento=formato,
                             id="api-" + hashlib.sha1((nome + defin["url"]).encode()).hexdigest()[:8])
                try:
                    achados = FonteGenerica(defin, chave).buscar(documento, "teste", max_paginas=1)
                except ErroFonte as e:
                    ultimo_erro = str(e)
                    passos.append(f"{rotulo} · autenticação {a or 'nenhuma'}: {e}")
                    auth_recusada = bool(re.search(r"HTTP (401|403)", str(e)))
                    break
                passos.append(f"{rotulo} · autenticação {a or 'nenhuma'} · documento "
                              f"{'formatado' if formato == 'formatado' else 'só dígitos'}: {len(achados)} processo(s).")
                if achados:
                    return _resultado(defin, achados, passos)
                sem_resultado = sem_resultado or defin
            if not auth_recusada:
                break  # a autenticação funcionou; trocar a forma de autenticar não muda o resultado
    if sem_resultado:
        passos.append("A API respondeu, mas nenhum processo foi reconhecido para este documento. "
                      "Teste com um CPF/CNPJ que sabidamente tenha processos.")
        return _resultado(sem_resultado, [], passos)
    raise ErroFonte(f"As tentativas não funcionaram. Último erro: {ultimo_erro}")


def _resultado(defin: dict, achados: list[Processo], passos: list[str]) -> dict:
    amostra = [{"numero": p.numero_cnj, "tribunal": p.tribunal, "classe": p.classe, "partes": len(p.partes),
                "andamentos": len(p.movimentacoes), "polo_do_consultado": p.polo_do_consultado} for p in achados[:5]]
    campos = sorted({k for p in achados for k, v in p.to_dict().items()
                     if v not in (None, "", [], {}) and k not in ("fontes", "envolvido", "numero_cnj")})
    return {"definicao": defin, "amostra": amostra, "total": len(achados), "campos_reconhecidos": campos,
            "passos": passos}
