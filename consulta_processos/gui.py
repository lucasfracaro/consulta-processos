"""Tela do programa: abre no navegador uma página local para consultar CPF/CNPJ e gerenciar fontes e chaves.

    python -m consulta_processos.gui

Só usa a biblioteca padrão do Python. O servidor escuta apenas em 127.0.0.1 e recusa pedidos vindos de
outros sites (proteção contra páginas maliciosas tentando usar o programa pelo navegador).
"""
from __future__ import annotations

import json
import logging
import os
import platform
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as cfgmod
from .__main__ import Investigacao, carregar_env, criar_parser
from .documento import identificar
from .fontes import catalogo, generica
from .imoveis import ficha as ficha_mod, itbi_sp
from .http import ErroFonte

PASTA_PROGRAMA = Path(__file__).resolve().parent.parent
SAIDA = PASTA_PROGRAMA / "relatorios"
PORTA_PADRAO = 8765
VERSAO = "2026.10.18-8"
log = logging.getLogger("consulta")


# ---------------------------------------------------------------- execução da consulta em segundo plano
class Job:
    def __init__(self, documento: str, opcoes: dict):
        self.documento, self.opcoes = documento, opcoes
        self.linhas: list[str] = []
        self.inv: Investigacao | None = None
        self.terminado = False
        self.erro: str | None = None
        self.resultado: dict = {}
        self.inicio = time.time()

    def status(self) -> dict:
        inv = self.inv
        return {
            "documento": self.documento, "nome": inv.nome if inv else None,
            "etapa": getattr(inv, "etapa", "preparando") if inv else "preparando",
            "processos": len(inv.processos) if inv else 0,
            "linhas": self.linhas[-80:], "terminado": self.terminado, "erro": self.erro,
            "relatorio": f"/r/{inv.base}.html" if inv and (SAIDA / f"{inv.base}.html").exists() else None,
            "resultado": self.resultado, "segundos": int(time.time() - self.inicio),
        }


class CapturaLog(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.job: Job | None = None

    def emit(self, record):
        if self.job is not None:
            prefixo = {"WARNING": "⚠ ", "ERROR": "✖ "}.get(record.levelname, "")
            self.job.linhas.append(f"{time.strftime('%H:%M:%S')}  {prefixo}{record.getMessage()}")


CAPTURA = CapturaLog()
JOB_ATUAL: Job | None = None
TRAVA = threading.Lock()
PENDENTES: dict[str, dict] = {}   # análises do assistente aguardando confirmação


def rodar_job(job: Job):
    CAPTURA.job = job
    try:
        argv = [job.documento, "--saida", str(SAIDA)]
        if not job.opcoes.get("socios", True):
            argv.append("--sem-socios")
        if job.opcoes.get("confirmados"):
            argv.append("--somente-confirmados")
        if job.opcoes.get("nome"):
            argv += ["--nome", job.opcoes["nome"]]
        args = criar_parser().parse_args(argv)
        doc = identificar(args.documento, args.tipo)
        log.info("Documento: %s %s", doc.tipo, doc.formatado)
        job.inv = Investigacao(args, doc)
        job.inv.executar()
        lista = list(job.inv.processos.values())
        job.resultado = {"total": len(lista),
                         "reu": sum(1 for p in lista if p.polo_do_consultado == "PASSIVO"),
                         "autor": sum(1 for p in lista if p.polo_do_consultado == "ATIVO"),
                         "alertas": sum(1 for p in lista if p.alertas)}
        log.info("Consulta concluída: %d processos.", len(lista))
    except ValueError as e:
        job.erro = str(e)
    except Exception as e:  # noqa: BLE001 — qualquer falha vira mensagem na tela
        log.exception("Falha na consulta")
        job.erro = f"{type(e).__name__}: {e}"
    finally:
        job.terminado = True


def historico() -> list[dict]:
    if not SAIDA.exists():
        return []
    itens = []
    for h in sorted(SAIDA.glob("dossie_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
        info = {"arquivo": h.name, "quando": time.strftime("%d/%m/%Y %H:%M", time.localtime(h.stat().st_mtime))}
        try:
            d = json.loads(h.with_suffix(".json").read_text("utf-8"))
            info.update(nome=d["consulta"].get("nome"), documento=d["consulta"].get("documento"),
                        total=d["resumo"].get("total_processos"), andamento=d["consulta"].get("em_andamento"))
        except Exception:  # noqa: BLE001
            pass
        itens.append(info)
    return itens


def abrir_pasta(caminho: Path):
    caminho.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Windows":
        os.startfile(caminho)  # type: ignore[attr-defined]
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(caminho)])
    else:
        subprocess.Popen(["xdg-open", str(caminho)])


# ---------------------------------------------------------------- gestão de fontes
def fontes_salvar(d: dict) -> dict:
    cfg = cfgmod.carregar()
    f = cfgmod.fonte(cfg, d["id"])
    if "ativa" in d:
        f["ativa"] = bool(d["ativa"])
    if d.get("remover_chave"):
        f.pop("chave", None)
    elif (d.get("chave") or "").strip():
        f["chave"] = d["chave"].strip()
    if isinstance(d.get("extra"), dict):
        f.setdefault("extra", {}).update({k: v.strip() for k, v in d["extra"].items() if isinstance(v, str)})
    f.pop("teste", None)
    cfgmod.salvar(cfg)
    return {"ok": True}


def fontes_testar(d: dict) -> dict:
    cfg = cfgmod.carregar()
    try:
        msg, ok = catalogo.testar(cfg, d["id"]), True
    except ErroFonte as e:
        msg, ok = str(e), False
    ok = ok and not msg.startswith(("Informe", "Pasta não"))
    cfgmod.fonte(cfg, d["id"])["teste"] = {"ok": ok, "mensagem": msg, "quando": time.strftime("%d/%m %H:%M")}
    cfgmod.salvar(cfg)
    return {"ok": ok, "mensagem": msg}


def fontes_analisar(d: dict) -> dict:
    nome = (d.get("nome") or "").strip() or "Nova fonte"
    endereco = (d.get("endereco") or "").strip()
    if not endereco.startswith(("http://", "https://")):
        raise ValueError("Informe o endereço completo da API (começando com https://).")
    doc = identificar(d.get("documento") or "", None)
    if doc.tipo == "RG":
        raise ValueError("Informe um CPF ou CNPJ válido para o teste.")
    auth = None
    if d.get("auth_tipo") in ("bearer", "header", "query"):
        auth = {"tipo": d["auth_tipo"], "nome": (d.get("auth_nome") or "").strip() or None}
    r = generica.analisar(nome, endereco, (d.get("chave") or "").strip() or None, doc, auth)
    token = secrets.token_hex(8)
    PENDENTES[token] = {"definicao": r["definicao"] | {"campos_reconhecidos": r["campos_reconhecidos"],
                                                      "site": d.get("site") or endereco},
                        "chave": (d.get("chave") or "").strip()}
    return {"token": token, **{k: v for k, v in r.items() if k != "definicao"},
            "endereco_final": r["definicao"]["url"], "metodo": r["definicao"].get("metodo")}


def fontes_adicionar(d: dict) -> dict:
    pend = PENDENTES.pop(d.get("token") or "", None)
    if not pend:
        raise ValueError("Análise expirada; rode o assistente de novo.")
    cfg = cfgmod.carregar()
    defin = pend["definicao"]
    cfg["personalizadas"] = [x for x in cfg["personalizadas"] if x["id"] != defin["id"]] + [defin]
    f = cfgmod.fonte(cfg, defin["id"])
    f["ativa"] = True
    if pend["chave"]:
        f["chave"] = pend["chave"]
    cfgmod.salvar(cfg)
    return {"ok": True, "id": defin["id"]}


def fontes_remover(d: dict) -> dict:
    cfg = cfgmod.carregar()
    cfg["personalizadas"] = [x for x in cfg["personalizadas"] if x["id"] != d.get("id")]
    cfg["fontes"].pop(d.get("id"), None)
    cfgmod.salvar(cfg)
    return {"ok": True}


# ---------------------------------------------------------------- imóveis
def imoveis_base() -> dict:
    return itbi_sp.status() | {"cidades": {k: v["nome"] for k, v in ficha_mod.CIDADES.items()}}


def imoveis_atualizar(_d=None) -> dict:
    if itbi_sp.PROGRESSO.rodando:
        return {"ok": True, "mensagem": "Já está baixando."}
    threading.Thread(target=itbi_sp.atualizar, daemon=True).start()
    return {"ok": True}


def _resumo_ficha(f: dict) -> dict:
    arq = ficha_mod.salvar_html(f)
    return {"encontrado": f["encontrado"], "titulo": ficha_mod.titulo(f["consulta"]), "imovel": f["imovel"],
            "indicadores": f["indicadores"], "alertas": f["alertas"], "transacoes": len(f["transacoes"]),
            "relatorio": f"/r/{arq.name}"}


def imoveis_matricula(d: dict) -> dict:
    mat = itbi_sp.matricula_num(d.get("matricula"))
    try:
        cart = int(d.get("cartorio") or 0)
    except ValueError:
        cart = 0
    if not mat or not 1 <= cart <= 18:
        raise ValueError("Informe o número da matrícula e o cartório (1º a 18º Registro de Imóveis de São Paulo).")
    if not itbi_sp.status()["pronta"]:
        imoveis_atualizar()
        return {"precisa_base": True}
    achados = itbi_sp.buscar_matricula(cart, mat)
    return _resumo_ficha(ficha_mod.montar(achados, {"matricula": mat, "cartorio": cart}))


def imoveis_carne(d: dict) -> dict:
    cidade = d.get("cidade")
    info = ficha_mod.CIDADES.get(cidade)
    if not info:
        raise ValueError("Escolha a cidade.")
    insc = (d.get("inscricao") or "").strip()
    if not insc:
        raise ValueError("Informe o número de inscrição do carnê do IPTU.")
    if cidade != "sao_paulo":
        return {"externo": True, "cidade": info["nome"], "portal": info["portal"], "inscricao": insc}
    sql = itbi_sp.sql_num(insc)
    if not sql or len(sql) != 11:
        raise ValueError("O SQL de São Paulo tem 11 dígitos (ex.: 000.000.0000-0).")
    if not itbi_sp.status()["pronta"]:
        imoveis_atualizar()
        return {"precisa_base": True}
    return _resumo_ficha(ficha_mod.montar(itbi_sp.buscar_sql(sql), {"sql": itbi_sp.sql_formatado(sql)}))


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _origem_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _json(self, obj, codigo=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if not self._origem_ok():
            return self.send_error(403)
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            corpo = PAGINA.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(corpo)
        elif url.path == "/ping":
            self._json({"ok": True, "app": "consulta_processos", "versao": VERSAO})
        elif url.path == "/status":
            self._json(JOB_ATUAL.status() if JOB_ATUAL else {"ocioso": True})
        elif url.path == "/historico":
            self._json(historico())
        elif url.path == "/imoveis/base":
            self._json(imoveis_base())
        elif url.path == "/fontes":
            self._json(catalogo.listar(cfgmod.carregar()))
        elif url.path.startswith("/r/"):
            arq = SAIDA / Path(urllib.parse.unquote(url.path[3:])).name   # impede sair da pasta
            if not arq.exists() or arq.suffix not in (".html", ".csv", ".json"):
                return self.send_error(404)
            tipo = {".html": "text/html", ".csv": "text/csv", ".json": "application/json"}[arq.suffix]
            self.send_response(200)
            self.send_header("Content-Type", f"{tipo}; charset=utf-8")
            if arq.suffix != ".html":
                self.send_header("Content-Disposition", f'attachment; filename="{arq.name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(arq.read_bytes())
        else:
            self.send_error(404)

    def do_POST(self):
        global JOB_ATUAL
        # Só aceita pedidos da própria página (cabeçalho próprio + host local).
        if not self._origem_ok() or self.headers.get("X-Consulta") != "1":
            return self.send_error(403)
        url = urllib.parse.urlparse(self.path)
        tamanho = int(self.headers.get("Content-Length") or 0)
        dados = json.loads(self.rfile.read(tamanho) or b"{}") if tamanho else {}
        try:
            if url.path == "/consultar":
                doc = identificar((dados.get("documento") or "").strip(), None)
                if doc.tipo == "RG" and not dados.get("nome"):
                    raise ValueError("Documento inválido: confira os dígitos do CPF ou CNPJ.")
                with TRAVA:
                    if JOB_ATUAL and not JOB_ATUAL.terminado:
                        return self._json({"erro": "Já existe uma consulta em andamento. Aguarde ela terminar."}, 409)
                    JOB_ATUAL = Job(dados["documento"].strip(), dados)
                    threading.Thread(target=rodar_job, args=(JOB_ATUAL,), daemon=True).start()
                return self._json({"ok": True})
            rotas = {"/fontes/salvar": fontes_salvar, "/fontes/testar": fontes_testar,
                     "/fontes/analisar": fontes_analisar, "/fontes/adicionar": fontes_adicionar,
                     "/fontes/remover": fontes_remover, "/imoveis/base/atualizar": imoveis_atualizar,
                     "/imoveis/matricula": imoveis_matricula, "/imoveis/carne": imoveis_carne}
            if url.path in rotas:
                return self._json(rotas[url.path](dados))
            if url.path == "/abrir-pasta":
                abrir_pasta(SAIDA)
                return self._json({"ok": True})
            if url.path == "/sair":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
        except (ValueError, ErroFonte) as e:
            return self._json({"erro": str(e)}, 400)
        self.send_error(404)


def porta_em_uso(porta: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", porta)) == 0


def main():
    os.chdir(PASTA_PROGRAMA)  # cache e relatórios sempre na pasta do programa
    if sys.stdout is None or sys.stderr is None:   # Windows sem console (pythonw): mensagens vão para um arquivo
        log_arq = open(PASTA_PROGRAMA / "consulta.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log_arq
        sys.stderr = sys.stderr or log_arq
    carregar_env()
    cfgmod.carregar()         # cria o config.json e importa chaves antigas do .env
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("consulta").addHandler(CAPTURA)
    porta = PORTA_PADRAO
    if porta_em_uso(porta):
        try:
            info = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{porta}/ping", timeout=3).read())
        except Exception:  # noqa: BLE001
            info = {}
        if info.get("versao") == VERSAO:
            webbrowser.open(f"http://127.0.0.1:{porta}/")
            return
        # versão anterior aberta: pede para ela encerrar e assume a porta
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{porta}/sair", data=b"{}", method="POST",
                                                          headers={"X-Consulta": "1"}), timeout=3)
        except Exception:  # noqa: BLE001
            pass
        for _ in range(20):
            if not porta_em_uso(porta):
                break
            time.sleep(0.25)
        else:
            print(f"A porta {porta} está ocupada por outro programa. Feche-o e tente de novo.")
            return
    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
    endereco = f"http://127.0.0.1:{porta}/"
    print(f"Consulta de processos aberta em {endereco}")
    threading.Timer(0.6, lambda: webbrowser.open(endereco)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


PAGINA = (Path(__file__).with_name("tela.html")).read_text("utf-8")

if __name__ == "__main__":
    sys.exit(main())
