"""Base local das transações imobiliárias da cidade de São Paulo (dados abertos de ITBI da Prefeitura).

A Prefeitura publica uma planilha por ano (desde 2006) com todas as transações que pagaram ITBI.
Este módulo descobre os links na página oficial, baixa cada planilha uma única vez, guarda o
conteúdo num índice SQLite local e apaga a planilha. Depois disso a busca por matrícula + cartório
ou pelo SQL (número do contribuinte do IPTU) é instantânea e não depende de internet.

As colunas são reconhecidas pelo nome do cabeçalho, para tolerar mudanças de layout entre os anos.
"""
from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from .xlsx import data_excel, ler

log = logging.getLogger("consulta")
PAGINA = "https://prefeitura.sp.gov.br/web/fazenda/w/acesso_a_informacao/31501"
PASTA = Path(__file__).resolve().parent.parent.parent / "dados"
BANCO = PASTA / "itbi_sp.sqlite"

# campo -> padrões no cabeçalho (normalizado: minúsculas, sem acento). A ordem importa.
CAMPOS = [
    ("sql", ["cadastro (sql)", "(sql)", "cadastro", "sql"]),
    ("logradouro", ["nome do logradouro", "logradouro"]),
    ("numero", ["numero"]),
    ("complemento", ["complemento"]),
    ("bairro", ["bairro"]),
    ("referencia", ["referencia"]),
    ("cep", ["cep"]),
    ("natureza", ["natureza"]),
    ("valor_transacao", ["valor de transacao", "valor da transacao", "valor declarado"]),
    ("data_transacao", ["data de transacao", "data da transacao"]),
    ("vvr_proporcional", ["venal de referencia (proporcional)", "referencia proporcional"]),
    ("valor_venal_ref", ["valor venal de referencia", "venal de referencia"]),
    ("proporcao", ["proporcao transmitida", "proporcao"]),
    ("base_calculo", ["base de calculo"]),
    ("tipo_financiamento", ["tipo de financiamento"]),
    ("valor_financiado", ["valor financiado"]),
    ("cartorio", ["cartorio"]),
    ("matricula", ["matricula"]),
    ("situacao_sql", ["situacao do sql", "situacao"]),
    ("area_terreno", ["area do terreno"]),
    ("testada", ["testada"]),
    ("fracao_ideal", ["fracao ideal"]),
    ("area_construida", ["area construida"]),
    ("uso_desc", ["descricao do uso"]),
    ("uso", ["uso"]),
    ("padrao_desc", ["descricao do padrao"]),
    ("padrao", ["padrao"]),
    ("acc", ["acc", "ano da construcao", "ano de construcao"]),
]
COLUNAS = [c for c, _ in CAMPOS]
ORDINAIS = {"primeiro": 1, "segundo": 2, "terceiro": 3, "quarto": 4, "quinto": 5, "sexto": 6, "setimo": 7,
            "oitavo": 8, "nono": 9, "decimo": 10, "decimo primeiro": 11, "decimo segundo": 12,
            "decimo terceiro": 13, "decimo quarto": 14, "decimo quinto": 15, "decimo sexto": 16,
            "decimo setimo": 17, "decimo oitavo": 18}


def _n(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def cartorio_num(v) -> int | None:
    """'3º Cartório de Registro de Imóveis', '03', '3 CRI', 'Terceiro Oficial' -> 3"""
    t = _n(v)
    m = re.search(r"\d+", t)
    if m:
        n = int(m.group(0))
        return n if 1 <= n <= 30 else None
    for nome, n in sorted(ORDINAIS.items(), key=lambda kv: -len(kv[0])):
        if t.startswith(nome):
            return n
    return None


def matricula_num(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    d = re.sub(r"\D", "", str(v)).lstrip("0")
    return d or None


def sql_num(v) -> str | None:
    d = re.sub(r"\D", "", str(v or ""))
    return d.zfill(11) if 9 <= len(d) <= 11 else (d or None)


def sql_formatado(d: str | None) -> str | None:
    if not d or len(d) != 11:
        return d
    return f"{d[:3]}.{d[3:6]}.{d[6:10]}-{d[10]}"


def _mapear_cabecalho(linha: list) -> dict[str, int] | None:
    nomes = [_n(c) for c in linha]
    if not any("cadastro" in n or "sql" in n for n in nomes) or not any("logradouro" in n or "transacao" in n for n in nomes):
        return None
    mapa: dict[str, int] = {}
    usados: set[int] = set()
    bases = [re.sub(r"\s*\(.*?\)\s*", " ", n).strip() for n in nomes]   # "uso (iptu)" -> "uso"
    for campo, padroes in CAMPOS:
        for p in padroes:
            idx = next((i for i, n in enumerate(nomes) if i not in usados and n and
                        (n == p or bases[i] == p or (len(p) > 3 and p in n))), None)
            if idx is not None:
                mapa[campo] = idx; usados.add(idx)
                break
    return mapa if "sql" in mapa else None


# ---------------------------------------------------------------- banco local
_TRAVA = threading.Lock()


def conectar() -> sqlite3.Connection:
    PASTA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BANCO, check_same_thread=False)
    con.execute(f"CREATE TABLE IF NOT EXISTS itbi (arquivo TEXT, aba TEXT, cartorio_n INTEGER, matricula_n TEXT, "
                f"sql_n TEXT, {', '.join(c + ' TEXT' for c in COLUNAS)})")
    con.execute("CREATE INDEX IF NOT EXISTS ix_mat ON itbi(cartorio_n, matricula_n)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_sql ON itbi(sql_n)")
    con.execute("CREATE TABLE IF NOT EXISTS arquivos (url TEXT PRIMARY KEY, nome TEXT, linhas INTEGER, quando TEXT, "
                "colunas TEXT)")
    return con


class Progresso:
    def __init__(self):
        self.rodando = False
        self.mensagem = ""
        self.atual = 0
        self.total = 0
        self.erro: str | None = None
        self.terminou_em: str | None = None

    def dict(self):
        return dict(rodando=self.rodando, mensagem=self.mensagem, atual=self.atual, total=self.total,
                    erro=self.erro, terminou_em=self.terminou_em)


PROGRESSO = Progresso()


def status() -> dict:
    if not BANCO.exists():
        return {"pronta": False, "transacoes": 0, "arquivos": 0, "anos": [], "progresso": PROGRESSO.dict()}
    con = conectar()
    try:
        n = con.execute("SELECT COUNT(*) FROM itbi").fetchone()[0]
        arqs = con.execute("SELECT nome, linhas, quando, colunas FROM arquivos ORDER BY nome").fetchall()
        com_mat = con.execute("SELECT COUNT(*) FROM itbi WHERE matricula_n IS NOT NULL").fetchone()[0]
        datas = con.execute("SELECT MIN(data_transacao), MAX(data_transacao) FROM itbi "
                            "WHERE data_transacao LIKE '____-__-__'").fetchone()
    finally:
        con.close()
    colunas = sorted({c for a in arqs for c in json.loads(a[3] or "[]")})
    return {"pronta": n > 0, "transacoes": n, "com_matricula": com_mat, "arquivos": len(arqs),
            "periodo": [datas[0], datas[1]], "ultima_atualizacao": max((a[2] for a in arqs), default=None),
            "colunas_reconhecidas": colunas, "progresso": PROGRESSO.dict()}


# ---------------------------------------------------------------- download e indexação
def _abrir(url: str, timeout=120):
    from ..http import _SSL
    url = urllib.parse.quote(url, safe=":/%()?=&#+~-._")
    req = urllib.request.Request(url, headers={"User-Agent": "consulta-processos/1.0"})
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL)


def descobrir_links(pagina: str | None = None) -> list[str]:
    """Lê a página oficial e devolve os links das planilhas .xlsx de ITBI (um por ano)."""
    pagina = pagina or PAGINA
    with _abrir(pagina, timeout=60) as r:
        conteudo = r.read().decode("utf-8", errors="replace")
    links = []
    for href in re.findall(r'href\s*=\s*["\']([^"\']+)["\']', conteudo, re.I):
        href = html.unescape(href).strip()
        if re.search(r"itbi", href, re.I) and href.lower().endswith(".xlsx"):
            links.append(urllib.parse.urljoin(pagina, href))
    return list(dict.fromkeys(links))


def _baixar(url: str, destino: Path) -> None:
    with _abrir(url) as r, open(destino, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        lido = 0
        while True:
            bloco = r.read(1 << 20)
            if not bloco:
                break
            f.write(bloco); lido += len(bloco)
            if total:
                PROGRESSO.mensagem = (f"Baixando {Path(urllib.parse.unquote(url)).name} — "
                                      f"{lido / 1e6:.0f} de {total / 1e6:.0f} MB")


def indexar_arquivo(con: sqlite3.Connection, caminho: Path, url: str) -> tuple[int, list[str]]:
    nome = Path(urllib.parse.unquote(url)).name
    con.execute("DELETE FROM itbi WHERE arquivo = ?", (url,))
    mapa: dict[str, int] | None = None
    aba_atual = None
    lote, total, colunas = [], 0, set()
    for aba, linha in ler(str(caminho)):
        if aba != aba_atual:
            aba_atual, mapa = aba, None
        if mapa is None:
            mapa = _mapear_cabecalho(linha)
            if mapa:
                colunas.update(mapa)
            continue
        if not any(v not in (None, "") for v in linha):
            continue
        reg = {c: (linha[i] if i < len(linha) else None) for c, i in mapa.items()}
        sqln = sql_num(reg.get("sql"))
        if not sqln:
            continue
        reg["data_transacao"] = data_excel(reg.get("data_transacao"))
        valores = [None if reg.get(c) in (None, "") else str(reg.get(c)) for c in COLUNAS]
        lote.append((url, aba, cartorio_num(reg.get("cartorio")), matricula_num(reg.get("matricula")), sqln, *valores))
        if len(lote) >= 5000:
            con.executemany(f"INSERT INTO itbi VALUES ({','.join('?' * (5 + len(COLUNAS)))})", lote)
            total += len(lote); lote = []
            PROGRESSO.mensagem = f"Organizando {nome}: {total:,} transações".replace(",", ".")
    if lote:
        con.executemany(f"INSERT INTO itbi VALUES ({','.join('?' * (5 + len(COLUNAS)))})", lote)
        total += len(lote)
    con.execute("INSERT OR REPLACE INTO arquivos VALUES (?,?,?,?,?)",
                (url, nome, total, time.strftime("%Y-%m-%d %H:%M"), json.dumps(sorted(colunas))))
    con.commit()
    return total, sorted(colunas)


def atualizar(pagina: str | None = None, links: list[str] | None = None) -> dict:
    """Baixa e indexa as planilhas que ainda não estão na base (e as que mudaram de nome/versão)."""
    if not _TRAVA.acquire(blocking=False):
        return {"ok": False, "mensagem": "Atualização já em andamento."}
    PROGRESSO.__init__()
    PROGRESSO.rodando = True
    try:
        PROGRESSO.mensagem = "Consultando a página de dados abertos da Prefeitura…"
        links = links or descobrir_links(pagina)
        if not links:
            raise RuntimeError("Nenhuma planilha de ITBI encontrada na página da Prefeitura (o endereço pode ter mudado).")
        con = conectar()
        try:
            feitos = {r[0] for r in con.execute("SELECT url FROM arquivos")}
            # remove versões antigas que saíram da página (ex.: planilha do ano corrente, atualizada todo mês)
            for velho in feitos - set(links):
                con.execute("DELETE FROM itbi WHERE arquivo = ?", (velho,))
                con.execute("DELETE FROM arquivos WHERE url = ?", (velho,))
            con.commit()
            pendentes = [u for u in links if u not in feitos]
            PROGRESSO.total = len(pendentes)
            for i, url in enumerate(pendentes, 1):
                PROGRESSO.atual = i
                tmp = PASTA / "baixando.xlsx"
                _baixar(url, tmp)
                n, cols = indexar_arquivo(con, tmp, url)
                tmp.unlink(missing_ok=True)
                log.info("ITBI: %s — %d transações (colunas: %s)", Path(urllib.parse.unquote(url)).name, n, ", ".join(cols))
        finally:
            con.close()
        PROGRESSO.mensagem = "Base atualizada."
        PROGRESSO.terminou_em = time.strftime("%d/%m/%Y %H:%M")
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        PROGRESSO.erro = f"{type(e).__name__}: {e}"
        log.error("ITBI: %s", PROGRESSO.erro)
        return {"ok": False, "mensagem": PROGRESSO.erro}
    finally:
        PROGRESSO.rodando = False
        _TRAVA.release()


# ---------------------------------------------------------------- busca
def _linhas(con, where: str, args: tuple) -> list[dict]:
    cur = con.execute(f"SELECT cartorio_n, matricula_n, sql_n, {', '.join(COLUNAS)} FROM itbi WHERE {where}", args)
    nomes = ["cartorio_n", "matricula_n", "sql_n"] + COLUNAS
    return [dict(zip(nomes, r)) for r in cur.fetchall()]


def buscar_matricula(cartorio: int, matricula: str) -> list[dict]:
    if not BANCO.exists():
        return []
    con = conectar()
    try:
        achados = _linhas(con, "cartorio_n = ? AND matricula_n = ?", (int(cartorio), matricula_num(matricula)))
        # completa com outras transações do mesmo SQL (quando a matrícula não foi preenchida na guia)
        sqls = {r["sql_n"] for r in achados}
        for s in sqls:
            achados += [r for r in _linhas(con, "sql_n = ?", (s,)) if r not in achados]
        return achados
    finally:
        con.close()


def buscar_sql(sql: str) -> list[dict]:
    if not BANCO.exists():
        return []
    con = conectar()
    try:
        return _linhas(con, "sql_n = ?", (sql_num(sql),))
    finally:
        con.close()
