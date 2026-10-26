"""API Pública do DataJud (CNJ) — oficial e gratuita.

LIMITE IMPORTANTE: o DataJud NÃO expõe partes nem CPF/CNPJ (Portaria CNJ 160/2020).
Por isso ele não "encontra" processos de uma pessoa; ele ENRIQUECE, pelo número CNJ,
os processos encontrados pelas outras fontes (capa oficial + movimentações completas).
Docs: https://datajud-wiki.cnj.jus.br/api-publica/
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from ..documento import alias_datajud, cnj_digitos
from ..http import ErroFonte, Interrompido, requisitar
from ..modelo import Movimentacao, Processo

log = logging.getLogger("consulta")
# Chave pública publicada pelo CNJ; ela pode mudar — atualize via DATAJUD_API_KEY.
CHAVE_PUBLICA = "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="
BASE = "https://api-publica.datajud.cnj.jus.br"


class DataJud:
    nome = "datajud"

    def __init__(self, api_key: str | None = None, paralelo: int = 4):
        self.headers = {"Authorization": f"APIKey {api_key or CHAVE_PUBLICA}",
                        "Content-Type": "application/json"}
        self.paralelo = paralelo

    def consultar(self, numero_cnj: str) -> list[Processo]:
        """Um mesmo número pode ter vários registros (1º grau, 2º grau, turma recursal)."""
        alias = alias_datajud(numero_cnj)
        if not alias:
            return []
        corpo = {"query": {"match": {"numeroProcesso": cnj_digitos(numero_cnj)}}, "size": 10}
        dados = requisitar("POST", f"{BASE}/api_publica_{alias}/_search",
                           headers=self.headers, json_body=corpo, cache_horas=12)
        return [self._converter(h.get("_source") or {}) for h in (dados.get("hits") or {}).get("hits", [])]

    def enriquecer(self, processos: dict[str, Processo]) -> None:
        def tarefa(numero):
            try:
                return numero, self.consultar(numero)
            except Interrompido:
                return numero, []
            except ErroFonte as e:
                log.warning("DataJud %s: %s", numero, e)
                return numero, []

        with ThreadPoolExecutor(self.paralelo) as ex:
            for numero, registros in ex.map(tarefa, list(processos)):
                for reg in registros:
                    processos[numero].mesclar(reg)

    @staticmethod
    def _converter(s: dict) -> Processo:
        movs = []
        for m in s.get("movimentos") or []:
            compl = "; ".join(
                f"{c.get('descricao') or c.get('nome')}: {c.get('nome') or c.get('valor')}"
                for c in m.get("complementosTabelados") or [])
            movs.append(Movimentacao(data=(m.get("dataHora") or "")[:10] or None,
                                     descricao=m.get("nome"), codigo=str(m.get("codigo") or ""),
                                     complemento=compl or None, fonte=f"datajud/{s.get('grau')}"))
        movs.sort(key=lambda m: m.data or "", reverse=True)
        classe = s.get("classe") or {}
        org = s.get("orgaoJulgador") or {}
        ajuiz = s.get("dataAjuizamento") or ""
        data_aj = f"{ajuiz[:4]}-{ajuiz[4:6]}-{ajuiz[6:8]}" if ajuiz[:8].isdigit() else ajuiz[:10] or None
        proc = Processo(
            numero_cnj=cnj_digitos(s.get("numeroProcesso")),
            tribunal=s.get("tribunal"),
            grau=s.get("grau"),
            classe=classe.get("nome"),
            classe_codigo=str(classe.get("codigo") or "") or None,
            assuntos=[a.get("nome") for a in s.get("assuntos") or [] if isinstance(a, dict) and a.get("nome")],
            orgao_julgador=org.get("nome"),
            sistema=(s.get("sistema") or {}).get("nome"),
            formato=(s.get("formato") or {}).get("nome"),
            data_distribuicao=data_aj,
            data_ultima_movimentacao=movs[0].data if movs else None,
            segredo_justica=bool(s.get("nivelSigilo")) if s.get("nivelSigilo") is not None else None,
            movimentacoes=movs,
            instancias=[f"{s.get('tribunal')} {s.get('grau')}"],
            fontes=["datajud"],
        )
        proc.informacoes_extras[f"DataJud {s.get('grau')} - última atualização"] = s.get("dataHoraUltimaAtualizacao")
        if org.get("codigoMunicipioIBGE"):
            proc.informacoes_extras[f"DataJud {s.get('grau')} - município IBGE"] = org["codigoMunicipioIBGE"]
        return proc
