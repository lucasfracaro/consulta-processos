"""DJEN — Diário de Justiça Eletrônico Nacional (CNJ / Comunica PJe). Oficial, gratuito, sem chave.

Principal fonte gratuita de DESCOBERTA: cada intimação/edital traz o número do processo e os
destinatários (nome + polo). Busca-se pelo nome e confirma-se a identidade quando o CPF/CNPJ
(inteiro ou nos dígitos visíveis de um CPF mascarado) aparece no texto da publicação.

Endpoint: GET https://comunicaapi.pje.jus.br/api/v1/comunicacao
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from difflib import SequenceMatcher

from ..documento import cnj_valido, so_digitos
from ..http import ErroFonte, requisitar
from ..modelo import Advogado, Movimentacao, Parte, Processo
from ..texto import limpar, papeis

log = logging.getLogger("consulta")
BASE = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
SUFIXOS = r"\b(LTDA|S\.?\s?A|S/A|EIRELI|ME|EPP|MEI|SOCIEDADE ANONIMA|LIMITADA)\b\.?"
RE_CNJ = re.compile(r"\b\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}\b")
RE_CPF = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
POLO = {"A": "ATIVO", "P": "PASSIVO", "T": "TERCEIRO"}
FORCA = {"DOCUMENTO_NO_TEXTO": 3, "NOME_EXATO": 2, "NOME_SEMELHANTE": 1, None: 0}


def normalizar_nome(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sem_sufixo(s: str) -> str:
    return normalizar_nome(re.sub(SUFIXOS, " ", (s or "").upper()))


@dataclass
class Alvo:
    """Quem estamos procurando: nome + documento completo (CNPJ/CPF) ou parcial (CPF mascarado)."""
    rotulo: str                  # "Empresa", "Pessoa", "Sócio: FULANO"
    nome: str
    documento: str | None = None  # só dígitos/alfanum, ou máscara com '*'
    tipo: str = "CPF"            # CPF | CNPJ

    def variantes(self) -> list[str]:
        vs = [self.nome.strip(), sem_sufixo(self.nome)]
        return list(dict.fromkeys(v for v in vs if v and len(v) >= 5))

    def doc_no_texto(self, texto: str) -> bool:
        if not self.documento or not texto:
            return False
        limpo = re.sub(r"[.\-/\s]", "", texto.upper())
        if "*" not in self.documento:
            return self.documento in limpo
        visiveis = [(i, c) for i, c in enumerate(self.documento) if c.isdigit()]
        if len(visiveis) < 5:
            return False
        for m in RE_CPF.finditer(texto):
            cpf = so_digitos(m.group())
            if all(cpf[i] == c for i, c in visiveis):
                return True
        return False


def texto_limpo(it: dict) -> str:
    """Texto da publicação sem HTML (calculado uma vez por publicação)."""
    if "_limpo" not in it:
        it["_limpo"] = limpar(it.get("texto"))
    return it["_limpo"]


def _sufixo(s: str) -> str:
    m = re.search(SUFIXOS, (s or "").upper())
    return re.sub(r"[^A-Z]", "", m.group(0)) if m else ""


def comparar_nomes(nome: str, alvo: str) -> str | None:
    """'EXATO', 'SEMELHANTE' ou None. Rígido de propósito: uma palavra a mais ou a menos
    (ex.: 'SAO MARCOS ...' x 'MARCOS ...') indica outra pessoa/empresa."""
    a, b = sem_sufixo(nome).split(), sem_sufixo(alvo).split()
    if not a or not b:
        return None
    sa, sb = _sufixo(nome), _sufixo(alvo)
    tipo_diferente = bool(sa and sb and sa != sb)   # ex.: LTDA x SA
    if a == b:
        return "SEMELHANTE" if tipo_diferente else "EXATO"
    if sorted(a) == sorted(b):
        return "SEMELHANTE"
    if len(a) == len(b) and SequenceMatcher(None, " ".join(a), " ".join(b)).ratio() >= 0.93:
        return "SEMELHANTE"   # diferença de grafia/digitação, mesma quantidade de palavras
    return None


POLO_POR_PAPEL = {"AUTOR": "A", "AUTORA": "A", "REQUERENTE": "A", "EXEQUENTE": "A", "AGRAVANTE": "A", "APELANTE": "A",
                  "EMBARGANTE": "A", "RECLAMANTE": "A", "IMPETRANTE": "A", "RECORRENTE": "A", "CREDOR": "A",
                  "REU": "P", "RÉU": "P", "RE": "P", "RÉ": "P", "REQUERIDO": "P", "REQUERIDA": "P", "EXECUTADO": "P",
                  "EXECUTADA": "P", "AGRAVADO": "P", "AGRAVADA": "P", "APELADO": "P", "APELADA": "P", "EMBARGADO": "P",
                  "EMBARGADA": "P", "RECLAMADO": "P", "RECLAMADA": "P", "IMPETRADO": "P", "IMPETRADA": "P",
                  "RECORRIDO": "P", "RECORRIDA": "P", "DEVEDOR": "P", "ACUSADO": "P", "ACUSADA": "P",
                  "DENUNCIADO": "P", "DENUNCIADA": "P"}


class DJEN:
    nome = "djen"

    def __init__(self, itens_por_pagina: int = 50, pausa_s: float = 0.6, max_paginas: int = 200,
                 anos_janela: int = 6, similaridade_min: float = 0.90):
        self.ipp, self.pausa, self.max_paginas = itens_por_pagina, pausa_s, max_paginas
        self.anos_janela, self.sim_min = anos_janela, similaridade_min
        self.deve_parar = lambda: False  # o orquestrador troca por uma função que indica "interromper"

    # ----------------------------------------------------------- acesso à API
    def _paginar(self, params: dict) -> list[dict]:
        itens, pagina = [], 1
        while pagina <= self.max_paginas and not self.deve_parar():
            dados = requisitar("GET", BASE, params=params | {"pagina": pagina, "itensPorPagina": self.ipp},
                               headers={"Accept": "application/json"}, cache_horas=24, tentativas=5)
            lote = dados.get("items") or []
            itens.extend(lote)
            if not lote or len(itens) >= int(dados.get("count") or 0):
                break
            pagina += 1
            time.sleep(self.pausa)
        return itens

    def _por_nome(self, nome: str) -> list[dict]:
        try:
            return self._paginar({"nomeParte": nome})
        except ErroFonte as e:
            log.info("DJEN sem período falhou (%s); varrendo por janelas de 6 meses.", e)
        itens, fim = [], date.today()
        limite = fim - timedelta(days=365 * self.anos_janela)
        while fim > limite and not self.deve_parar():
            ini = max(fim - timedelta(days=182), limite)
            try:
                itens += self._paginar({"nomeParte": nome, "dataDisponibilizacaoInicio": ini.isoformat(),
                                        "dataDisponibilizacaoFim": fim.isoformat()})
            except ErroFonte as e:
                log.warning("DJEN %s..%s: %s", ini, fim, e)
            fim = ini - timedelta(days=1)
        return itens

    def _por_numero(self, numero: str) -> list[dict]:
        return self._paginar({"numeroProcesso": so_digitos(numero)})

    # ----------------------------------------------------------- casamento com o alvo
    def _casa(self, it: dict, alvo: Alvo) -> tuple[str | None, str | None]:
        """Retorna (nivel_de_confianca, polo) ou (None, None) se a publicação não é do alvo."""
        texto = texto_limpo(it)
        nivel, polo = None, None
        for d in it.get("destinatarios") or []:
            r = comparar_nomes(d.get("nome") or "", alvo.nome)
            if r == "EXATO" or (r == "SEMELHANTE" and nivel is None):
                nivel, polo = r, d.get("polo")
        if not polo:  # polo pelo papel escrito no texto (ex.: "AGRAVADO : FULANO")
            for papel, nome in papeis(texto):
                if comparar_nomes(nome, alvo.nome):
                    polo = POLO_POR_PAPEL.get(papel.replace("(A)", "").strip()); break
        if alvo.doc_no_texto(texto):
            return "DOCUMENTO_NO_TEXTO", polo
        if nivel == "EXATO":
            return "NOME_EXATO", polo
        if nivel == "SEMELHANTE":
            return "NOME_SEMELHANTE", polo
        return None, None

    # ----------------------------------------------------------- conversão
    @staticmethod
    def _aplicar(p: Processo, it: dict) -> None:
        texto = texto_limpo(it)
        data = (it.get("data_disponibilizacao") or it.get("datadisponibilizacao") or "")[:10] or None
        p.tribunal = p.tribunal or it.get("siglaTribunal")
        p.classe = p.classe or it.get("nomeClasse")
        p.classe_codigo = p.classe_codigo or (str(it["codigoClasse"]) if it.get("codigoClasse") else None)
        p.orgao_julgador = p.orgao_julgador or it.get("nomeOrgao")
        if not p.partes and it.get("destinatarios"):
            roles = {normalizar_nome(n): papel.title() for papel, n in papeis(texto) if not papel.startswith("ADVOGAD")}
            p.partes = [Parte(nome=d.get("nome"), polo=POLO.get(str(d.get("polo") or "").upper()[:1]),
                              tipo=roles.get(normalizar_nome(d.get("nome"))))
                        for d in it["destinatarios"]]
        if not p.partes and papeis(texto):
            # sem lista de destinatários: monta as partes pela qualificação escrita no texto
            atual = None
            for papel, nome in papeis(texto):
                if papel.startswith("ADVOGAD"):
                    if atual is not None:
                        oab = re.search(r"\(OAB\s*([^)]+)\)", nome)
                        atual.advogados.append(Advogado(re.sub(r"\s*\(OAB[^)]*\)", "", nome), [oab.group(1)] if oab else []))
                    continue
                atual = Parte(nome=nome, tipo=papel.title(), polo=POLO.get(POLO_POR_PAPEL.get(papel, "")))
                p.partes.append(atual)
        if it.get("destinatarioadvogados") and not any(pt.polo == "ADVOGADO" for pt in p.partes):
            advs = it.get("destinatarioadvogados") or []
            if advs:
                p.partes.append(Parte(nome="(advogados intimados)", polo="ADVOGADO", advogados=[
                    Advogado((x.get("advogado") or x).get("nome"),
                             [f"{(x.get('advogado') or x).get('numero_oab')}/{(x.get('advogado') or x).get('uf_oab')}"])
                    for x in advs]))
        if any(pb.get("id") == it.get("id") for pb in p.publicacoes):
            return
        p.publicacoes.append({
            "id": it.get("id"), "data": data, "tipo": it.get("tipoComunicacao"),
            "documento": it.get("tipoDocumento"), "orgao": it.get("nomeOrgao"), "texto": texto,
            "link": it.get("link"),
            "certidao": f"{BASE}/{it['hash']}/certidao" if it.get("hash") else None,
        })
        p.movimentacoes.append(Movimentacao(
            data=data, descricao=f"Publicação DJEN: {it.get('tipoComunicacao') or ''} {it.get('tipoDocumento') or ''}".strip(),
            complemento=texto[:400] + ("…" if len(texto) > 400 else ""), fonte="djen"))
        if data and (not p.data_ultima_movimentacao or data > p.data_ultima_movimentacao):
            p.data_ultima_movimentacao = data
        proprio = so_digitos(p.numero_cnj)
        for m in RE_CNJ.finditer(texto):
            n = so_digitos(m.group())
            if n != proprio and cnj_valido(n) and n not in p.processos_citados:
                p.processos_citados.append(n)

    def _registrar(self, processos: dict[str, Processo], it: dict, alvo: Alvo, nivel: str, polo) -> None:
        numero = so_digitos(it.get("numero_processo") or it.get("numeroprocessocommascara"))
        if len(numero) != 20:
            return
        p = processos.setdefault(numero, Processo(numero_cnj=numero, fontes=["djen"], envolvido=alvo.rotulo))
        if FORCA[nivel] > FORCA.get(p.como_foi_encontrado, 0):
            p.como_foi_encontrado = nivel
        if polo and not p.polo_do_consultado:
            p.polo_do_consultado = POLO.get(str(polo).upper()[:1], polo)
        if not p.papel_do_consultado:
            for papel, nome in papeis(texto_limpo(it)):
                if not papel.startswith("ADVOGAD") and comparar_nomes(nome, alvo.nome):
                    p.papel_do_consultado = papel.title(); break
        self._aplicar(p, it)

    # ----------------------------------------------------------- API pública do módulo
    def buscar(self, alvo: Alvo) -> list[Processo]:
        processos: dict[str, Processo] = {}
        vistos = set()
        for variante in alvo.variantes():
            if self.deve_parar():
                break
            log.info("  DJEN: '%s'...", variante)
            for it in self._por_nome(variante):
                chave = it.get("id") or it.get("hash")
                if chave in vistos:
                    continue
                vistos.add(chave)
                nivel, polo = self._casa(it, alvo)
                if nivel:
                    self._registrar(processos, it, alvo, nivel, polo)
        return list(processos.values())

    def verificar_numero(self, numero: str, alvos: list[Alvo]) -> Processo | None:
        """Busca as publicações de um número e devolve o processo só se algum alvo for parte nele."""
        itens = self._por_numero(numero)
        for alvo in alvos:
            processos: dict[str, Processo] = {}
            for it in itens:
                nivel, polo = self._casa(it, alvo)
                if nivel:
                    self._registrar(processos, it, alvo, nivel, polo)
            p = processos.get(so_digitos(numero))
            if p:
                for it in itens:
                    self._aplicar(p, it)
                return p
        return None

    def completar(self, p: Processo) -> None:
        """Baixa todas as publicações do processo (histórico completo de intimações)."""
        for it in self._por_numero(p.numero_cnj):
            self._aplicar(p, it)
        p.publicacoes.sort(key=lambda pb: pb.get("data") or "", reverse=True)
        p.movimentacoes.sort(key=lambda m: m.data or "", reverse=True)
