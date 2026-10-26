"""Judit API (paga) — busca assíncrona por CPF/CNPJ. Docs: https://docs.judit.io/"""
from __future__ import annotations

import logging
import time

from ..documento import Documento, so_digitos
from ..http import ErroFonte, dormir, requisitar
from ..modelo import Advogado, Movimentacao, Parte, Processo

log = logging.getLogger("consulta")
BASE = "https://requests.prod.judit.io"


def _nomes(lista) -> list[str]:
    return [x.get("name") or x.get("code") for x in lista or [] if isinstance(x, dict)]


class Judit:
    id = "judit"
    nome = "Judit"

    def __init__(self, chave: str, espera_max_s: int = 300):
        self.headers = {"api-key": chave, "Content-Type": "application/json"}
        self.espera_max_s = espera_max_s

    def testar(self) -> str:
        requisitar("GET", f"{BASE}/requests", headers=self.headers, params={"page": 1, "page_size": 1}, tentativas=1)
        return "Conexão e chave aceitas."

    def buscar(self, doc: Documento, rotulo: str) -> list[Processo]:
        if doc.tipo not in ("CPF", "CNPJ"):
            return []
        busca = {"search_type": doc.tipo.lower(), "search_key": doc.valor}
        req = requisitar("POST", f"{BASE}/requests", headers=self.headers, json_body={"search": busca})
        rid = req.get("request_id")
        if not rid:
            raise ErroFonte("Judit não devolveu o número da requisição")
        inicio, espera = time.time(), 3.0
        while time.time() - inicio < self.espera_max_s:
            st = requisitar("GET", f"{BASE}/requests/{rid}", headers=self.headers)
            if st.get("status") in ("completed", "done", "finished"):
                break
            dormir(espera); espera = min(espera * 1.5, 20)
        else:
            raise ErroFonte("Judit: tempo esgotado aguardando a resposta")
        processos, pagina = [], 1
        while True:
            dados = requisitar("GET", f"{BASE}/responses", headers=self.headers,
                               params={"request_id": rid, "page": pagina, "page_size": 100})
            for item in dados.get("page_data", []):
                rd = item.get("response_data") or {}
                if rd.get("code"):
                    processos.append(self._converter(rd, doc, rotulo))
            if pagina >= int(dados.get("page_count") or 1):
                break
            pagina += 1
        return processos

    @staticmethod
    def _converter(l: dict, doc: Documento, rotulo: str) -> Processo:
        proc = Processo(
            numero_cnj=so_digitos(l.get("code")), tribunal=l.get("tribunal_acronym"),
            grau=f"{l['instance']}º grau" if l.get("instance") else None,
            classe=(_nomes(l.get("classifications")) or [None])[0], assuntos=_nomes(l.get("subjects")),
            area=l.get("area"), orgao_julgador=(_nomes(l.get("courts")) or [None])[0], comarca_uf=l.get("state"),
            data_distribuicao=(l.get("distribution_date") or "")[:10] or None,
            data_ultima_movimentacao=((l.get("last_step") or {}).get("step_date") or "")[:10] or None,
            valor_causa=l.get("amount"), titulo_polo_ativo=l.get("name"),
            como_foi_encontrado="DOCUMENTO_NO_TEXTO", fontes=["judit"], envolvido=rotulo,
        )
        for p in l.get("parties") or []:
            lado = (p.get("side") or "").upper()
            polo = {"ACTIVE": "ATIVO", "PASSIVE": "PASSIVO", "INTERESTED": "TERCEIRO"}.get(lado, lado or None)
            docs = [so_digitos(p.get("main_document"))] + [so_digitos(d.get("document")) for d in p.get("documents") or []]
            proc.partes.append(Parte(nome=p.get("name"), polo=polo, tipo=p.get("person_type"),
                                     documento=p.get("main_document"),
                                     advogados=[Advogado(a.get("name")) for a in p.get("lawyers") or []]))
            if any(d and (d == doc.valor or (doc.tipo == "CNPJ" and d[:8] == doc.valor[:8])) for d in docs):
                proc.polo_do_consultado = polo
        for s in l.get("steps") or []:
            proc.movimentacoes.append(Movimentacao(data=(s.get("step_date") or "")[:10] or None,
                                                   descricao=s.get("content"), fonte="judit"))
        return proc
