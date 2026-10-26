"""Leitor de planilhas .xlsx só com a biblioteca padrão (um .xlsx é um zip de arquivos XML).

Lê em fluxo, linha a linha, para aguentar planilhas com centenas de milhares de linhas sem estourar
a memória. Devolve, para cada aba, as linhas como listas de valores (texto ou número).
"""
from __future__ import annotations

import re
import zipfile
from datetime import date, timedelta
from typing import Iterator
from xml.etree.ElementTree import iterparse

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _coluna(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _textos_compartilhados(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    saida = []
    with z.open("xl/sharedStrings.xml") as f:
        for ev, el in iterparse(f, events=("end",)):
            if el.tag == NS + "si":
                saida.append("".join(t.text or "" for t in el.iter(NS + "t")))
                el.clear()
    return saida


def _abas(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(nome da aba, caminho do XML)] na ordem do arquivo."""
    rels = {}
    with z.open("xl/_rels/workbook.xml.rels") as f:
        for _, el in iterparse(f):
            if el.tag.endswith("Relationship"):
                alvo = el.get("Target", "")
                alvo = alvo.lstrip("/")
                rels[el.get("Id")] = alvo if alvo.startswith("xl/") else "xl/" + alvo
    abas = []
    with z.open("xl/workbook.xml") as f:
        for _, el in iterparse(f):
            if el.tag == NS + "sheet":
                abas.append((el.get("name"), rels.get(el.get(NS_REL + "id"), "")))
    return abas


def ler(caminho: str) -> Iterator[tuple[str, list]]:
    """Gera (nome_da_aba, linha) para todas as linhas de todas as abas."""
    with zipfile.ZipFile(caminho) as z:
        compartilhados = _textos_compartilhados(z)
        for nome, xml in _abas(z):
            if xml not in z.namelist():
                continue
            with z.open(xml) as f:
                for _, el in iterparse(f, events=("end",)):
                    if el.tag != NS + "row":
                        continue
                    linha: list = []
                    for c in el.iter(NS + "c"):
                        ref, tipo = c.get("r"), c.get("t")
                        v = c.find(NS + "v")
                        if tipo == "s" and v is not None:
                            valor = compartilhados[int(v.text)] if v.text and int(v.text) < len(compartilhados) else ""
                        elif tipo == "inlineStr":
                            valor = "".join(t.text or "" for t in c.iter(NS + "t"))
                        elif v is not None and v.text is not None:
                            if tipo in ("str", "e"):
                                valor = v.text
                            elif tipo == "b":
                                valor = v.text == "1"
                            else:
                                try:
                                    num = float(v.text)
                                    valor = int(num) if num.is_integer() else num
                                except ValueError:
                                    valor = v.text
                        else:
                            valor = None
                        if ref:
                            i = _coluna(ref)
                            while len(linha) < i:
                                linha.append(None)
                        linha.append(valor)
                    el.clear()
                    yield nome, linha


def data_excel(v) -> str | None:
    """Converte número de série do Excel ou texto de data para AAAA-MM-DD."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and 1000 < v < 80000:
        return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
    t = str(v).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return m.group(0)
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return t[:10] or None
