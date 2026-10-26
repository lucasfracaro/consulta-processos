"""Escavador API v2 (paga) — processos por CPF/CNPJ. Docs: https://api.escavador.com/v2/docs/"""
from __future__ import annotations

import logging

from ..documento import Documento, so_digitos
from ..http import requisitar
from ..modelo import Advogado, Parte, Processo

log = logging.getLogger("consulta")
BASE = "https://api.escavador.com/api/v2"


class Escavador:
    id = "escavador"
    nome = "Escavador"

    def __init__(self, chave: str, max_paginas: int = 50):
        self.headers = {"Authorization": f"Bearer {chave}", "X-Requested-With": "XMLHttpRequest"}
        self.max_paginas = max_paginas

    def testar(self) -> str:
        requisitar("GET", f"{BASE}/tribunais", headers=self.headers, tentativas=1)
        return "Conexão e chave aceitas."

    def buscar(self, doc: Documento, rotulo: str) -> list[Processo]:
        if doc.tipo not in ("CPF", "CNPJ"):
            return []
        params = {"cpf_cnpj": doc.valor}
        if doc.tipo == "CNPJ":
            params["incluir_mesma_raiz_cnpj"] = 1
        url, processos = f"{BASE}/envolvido/processos", []
        for _ in range(self.max_paginas):
            dados = requisitar("GET", url, headers=self.headers, params=params, cache_horas=24)
            processos += [self._converter(item, doc, rotulo) for item in dados.get("items", [])]
            url, params = (dados.get("links") or {}).get("next"), None
            if not url:
                break
        return processos

    @staticmethod
    def _converter(p: dict, doc: Documento, rotulo: str) -> Processo:
        fontes = p.get("fontes") or []
        tribunais = [f for f in fontes if f.get("tipo") == "TRIBUNAL"] or fontes
        principal = max(tribunais, key=lambda f: (f.get("grau") or 0, bool(f.get("capa"))), default={})
        capa = principal.get("capa") or {}
        valor = (capa.get("valor_causa") or {}).get("valor")
        proc = Processo(
            numero_cnj=so_digitos(p.get("numero_cnj")), tribunal=principal.get("sigla"),
            grau=principal.get("grau_formatado"), classe=capa.get("classe"),
            classe_codigo=(capa.get("classe_normalizada") or {}).get("codigo_cnj"),
            assuntos=[a.get("path_completo") or a.get("nome") for a in capa.get("assuntos_normalizados") or []]
                     or ([capa["assunto"]] if capa.get("assunto") else []),
            area=capa.get("area"), orgao_julgador=capa.get("orgao_julgador"),
            comarca_uf=(p.get("estado_origem") or {}).get("sigla"), sistema=principal.get("sistema"),
            formato="Físico" if principal.get("fisico") else "Eletrônico",
            data_distribuicao=capa.get("data_distribuicao") or p.get("data_inicio"),
            data_arquivamento=capa.get("data_arquivamento"),
            data_ultima_movimentacao=p.get("data_ultima_movimentacao"),
            valor_causa=float(valor) if valor not in (None, "") else None,
            segredo_justica=principal.get("segredo_justica"),
            como_foi_encontrado="DOCUMENTO_NO_TEXTO" if p.get("tipo_match") in ("CPF", "CNPJ", "MATRIZ", "FILIAL")
                                else "NOME_SEMELHANTE",
            titulo_polo_ativo=p.get("titulo_polo_ativo"), titulo_polo_passivo=p.get("titulo_polo_passivo"),
            links=[f["url"] for f in fontes if f.get("url")], fontes=["escavador"], envolvido=rotulo,
        )
        for i in capa.get("informacoes_complementares") or []:
            proc.informacoes_extras[f"{i.get('tipo')}"] = i.get("valor")
        alvo = doc.valor
        for e in principal.get("envolvidos") or []:
            parte = Parte(nome=e.get("nome"), polo=e.get("polo"), tipo=e.get("tipo_normalizado") or e.get("tipo"),
                          tipo_pessoa=e.get("tipo_pessoa"), documento=e.get("cpf") or e.get("cnpj"),
                          advogados=[Advogado(a.get("nome"), [f"{o.get('uf')}{o.get('numero')}" for o in a.get("oabs") or []])
                                     for a in e.get("advogados") or []])
            proc.partes.append(parte)
            d = (parte.documento or "").upper()
            if d and (d == alvo or (doc.tipo == "CNPJ" and d[:8] == alvo[:8])):
                proc.polo_do_consultado, proc.papel_do_consultado = parte.polo, parte.tipo
        return proc
