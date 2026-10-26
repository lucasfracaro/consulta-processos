"""PGFN — Dados abertos da Dívida Ativa da União, Previdenciária e FGTS (oficial, gratuito).

A PGFN publica trimestralmente CSVs por UF. Não há API de consulta, então o programa varre os
arquivos baixados (zip ou csv) numa pasta local. INDICADOR_AJUIZADO = "SIM" indica que já
existe execução fiscal no Judiciário.
Download: https://www.gov.br/pgfn/pt-br/assuntos/divida-ativa-da-uniao/transparencia-fiscal-1/dados-abertos
As colunas são detectadas pelo nome, para tolerar mudanças de layout entre trimestres.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from pathlib import Path

from ..documento import Documento, so_digitos
from .djen import normalizar_nome

log = logging.getLogger("consulta")
csv.field_size_limit(10_000_000)


def _coluna(cabecalho: list[str], *candidatos: str) -> str | None:
    up = {c.upper().strip(): c for c in cabecalho}
    for cand in candidatos:
        for k, orig in up.items():
            if cand in k:
                return orig
    return None


def _valor(s: str | None) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _cpf_mascarado_bate(campo: str, cpf: str) -> bool:
    """CPFs podem vir mascarados (ex.: ***.456.789-**): compara só os dígitos visíveis."""
    limpo = re.sub(r"[.\-/\s]", "", campo)
    if len(limpo) != 11:
        return False
    return all(c == d for c, d in zip(limpo, cpf) if c.isdigit())


def _arquivos(pasta: Path):
    for arq in sorted(pasta.rglob("*")):
        if arq.suffix.lower() == ".csv":
            with arq.open("r", encoding="latin-1", newline="") as f:
                yield arq.name, f
        elif arq.suffix.lower() == ".zip":
            with zipfile.ZipFile(arq) as z:
                for m in z.namelist():
                    if m.lower().endswith(".csv"):
                        with z.open(m) as bruto:
                            yield f"{arq.name}/{m}", io.TextIOWrapper(bruto, encoding="latin-1", newline="")


def buscar(pasta: str, doc: Documento, nome: str | None) -> dict:
    p = Path(pasta)
    if not p.exists():
        log.warning("PGFN: pasta %s não existe.", pasta)
        return {}
    if doc.tipo == "RG":
        log.info("PGFN: dados indexados por CPF/CNPJ; RG não se aplica.")
        return {}
    alvo_nome = normalizar_nome(nome) if nome else None
    inscricoes = []
    for origem, f in _arquivos(p):
        leitor = csv.reader(f, delimiter=";")
        cab = next(leitor, None)
        if not cab:
            continue
        c_doc = _coluna(cab, "CPF_CNPJ", "CPF/CNPJ", "CNPJ")
        if not c_doc:
            continue
        idx = {c: i for i, c in enumerate(cab)}
        i_doc = idx[c_doc]
        cols = {k: _coluna(cab, *v) for k, v in {
            "nome": ("NOME_DEVEDOR", "NOME"), "uf": ("UF",), "inscricao": ("NUMERO_INSCRICAO", "INSCRICAO"),
            "situacao": ("SITUACAO_INSCRICAO",), "tipo_situacao": ("TIPO_SITUACAO",),
            "receita": ("RECEITA_PRINCIPAL", "RECEITA"), "data": ("DATA_INSCRICAO",),
            "ajuizado": ("AJUIZADO",), "valor": ("VALOR_CONSOLIDADO", "VALOR"),
            "tipo_devedor": ("TIPO_DEVEDOR",), "unidade": ("UNIDADE_RESPONSAVEL",)}.items()}
        for linha in leitor:
            if len(linha) <= i_doc:
                continue
            campo = linha[i_doc]
            if doc.tipo == "CNPJ":
                ok = so_digitos(campo) == doc.valor or re.sub(r"[.\-/\s]", "", campo).upper() == doc.valor
            else:
                ok = _cpf_mascarado_bate(campo, doc.valor)
                if ok and alvo_nome and cols["nome"]:  # máscara gera colisões: confirma pelo nome
                    ok = normalizar_nome(linha[idx[cols["nome"]]]) == alvo_nome
            if not ok:
                continue
            reg = {k: (linha[idx[c]].strip() if c and idx[c] < len(linha) else None) for k, c in cols.items()}
            reg["arquivo"] = origem
            reg["valor"] = _valor(reg.get("valor"))
            inscricoes.append(reg)
    if not inscricoes:
        return {"inscricoes": [], "total": 0}
    return {
        "inscricoes": inscricoes,
        "total": len(inscricoes),
        "valor_total": round(sum(i["valor"] for i in inscricoes), 2),
        "ajuizadas": sum(1 for i in inscricoes if (i.get("ajuizado") or "").upper().startswith("S")),
    }
