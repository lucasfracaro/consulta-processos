"""Cliente HTTP com ritmo controlado por servidor, respeito ao limite de requisições (HTTP 429),
retentativas, cache em disco e interrupção imediata.

Cada servidor tem um "limitador": um intervalo mínimo entre requisições, compartilhado por todas as
threads. O intervalo se ajusta sozinho:
- se o servidor informa o limite (cabeçalho X-RateLimit-Limit), o ritmo passa a ser limite/minuto;
- se restam poucas requisições na janela (X-RateLimit-Remaining), o programa desacelera;
- se o servidor responde 429, o programa espera o tempo pedido (Retry-After), dobra o intervalo e
  tenta de novo, sem contar isso como falha;
- depois de uma sequência de sucessos, o ritmo volta a acelerar aos poucos.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import ssl
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("consulta")
CACHE_DIR = Path(".cache_consultas")
PARAR = threading.Event()   # sinal global de "interromper a consulta"
MAX_ESPERA_429 = 15 * 60    # desiste de uma requisição só depois de 15 min sendo limitada


class ErroFonte(Exception):
    pass


class Resposta:
    """Resposta HTTP mínima (só biblioteca padrão — o programa não depende de pacotes externos)."""
    def __init__(self, status: int, headers, corpo: bytes):
        self.status_code, self.headers, self.content = status, headers, corpo or b""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class ErroRede(Exception):
    pass


_SSL = ssl.create_default_context()   # usa os certificados do sistema (inclusive no Windows)


def _http(metodo: str, url: str, headers=None, params=None, json_body=None, timeout=60) -> Resposta:
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
    dados = None
    cab = {"User-Agent": "consulta-processos/1.0", "Accept-Encoding": "identity", **(headers or {})}
    if json_body is not None:
        dados = json.dumps(json_body).encode("utf-8")
        cab.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=dados, headers=cab, method=metodo.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return Resposta(r.status, r.headers, r.read())
    except urllib.error.HTTPError as e:
        return Resposta(e.code, e.headers, e.read() if e.fp else b"")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise ErroRede(str(getattr(e, "reason", e))) from e


class Interrompido(ErroFonte):
    pass


def dormir(segundos: float) -> None:
    """Espera que acorda na hora se a consulta for interrompida."""
    if segundos > 0 and PARAR.wait(segundos):
        raise Interrompido("consulta interrompida")


class Limitador:
    def __init__(self, nome: str, intervalo: float, maximo: float = 20.0):
        self.nome, self.base, self.intervalo, self.maximo = nome, intervalo, intervalo, maximo
        self.proximo = 0.0
        self.sucessos = 0
        self.ultimo_aviso = 0.0
        self.trava = threading.Lock()

    def aguardar(self) -> None:
        with self.trava:
            agora = time.monotonic()
            inicio = max(agora, self.proximo)
            self.proximo = inicio + self.intervalo
        dormir(inicio - time.monotonic())

    def sucesso(self, r: Resposta) -> None:
        with self.trava:
            limite = _int(r.headers.get("X-RateLimit-Limit"))
            restante = _int(r.headers.get("X-RateLimit-Remaining"))
            if limite:
                # assume janela de 1 minuto (padrão dessas APIs) e usa 90% do limite
                self.base = max(self.base, 60.0 / (limite * 0.9))
                self.intervalo = max(self.intervalo, self.base)
            if restante is not None and restante <= 2:
                self.proximo = max(self.proximo, time.monotonic() + max(self.intervalo * 3, 5))
            self.sucessos += 1
            if self.sucessos >= 20 and self.intervalo > self.base:
                self.intervalo = max(self.base, self.intervalo * 0.8)
                self.sucessos = 0

    def limitado(self, espera: float) -> None:
        with self.trava:
            self.sucessos = 0
            self.intervalo = min(self.maximo, self.intervalo * 2)
            self.proximo = max(self.proximo, time.monotonic() + espera)
            agora = time.monotonic()
            avisar = agora - self.ultimo_aviso > 30
            if avisar:
                self.ultimo_aviso = agora
        if avisar:
            log.warning("%s pediu para desacelerar (limite de requisições). Aguardando %.0f s e seguindo "
                        "em ritmo mais lento (1 requisição a cada %.1f s).", self.nome, espera, self.intervalo)


LIMITADORES = {
    "comunicaapi.pje.jus.br": Limitador("DJEN (CNJ)", 1.2),
    "api-publica.datajud.cnj.jus.br": Limitador("DataJud (CNJ)", 0.35),
    "api.portaldatransparencia.gov.br": Limitador("Portal da Transparência", 0.8),
    "certidoes-apf.apps.tcu.gov.br": Limitador("TCU", 1.0),
}
PADRAO = Limitador("servidor", 0.3)


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _tempo_espera(r: Resposta, tentativa_429: int) -> float:
    """Usa o tempo pedido pelo servidor; sem essa informação, espera 5, 10, 20, 40, 60 s…"""
    ra = _int(r.headers.get("Retry-After"))
    if ra is not None and ra >= 0:
        return max(2.0, ra + 1.0)
    reset = _int(r.headers.get("X-RateLimit-Reset"))
    if reset and reset > 1e9:
        return max(2.0, reset - time.time() + 1.0)
    return min(5.0 * 2 ** (tentativa_429 - 1), 60.0)


def _resumo_erro(r: Resposta) -> str:
    try:
        corpo = r.json()
        msg = corpo.get("message") or corpo.get("mensagem") or corpo.get("error") if isinstance(corpo, dict) else None
    except ValueError:
        msg = None
    msg = msg or re.sub(r"\s+", " ", r.text or "")[:150]
    return f"HTTP {r.status_code} ({msg})"


def requisitar(metodo: str, url: str, *, headers=None, params=None, json_body=None,
               timeout=60, tentativas=4, cache_horas: float = 0) -> dict | list:
    chave = hashlib.sha256(json.dumps([metodo, url, params, json_body], sort_keys=True,
                                      default=str).encode()).hexdigest()
    arq = CACHE_DIR / f"{chave}.json"
    if cache_horas and arq.exists() and time.time() - arq.stat().st_mtime < cache_horas * 3600:
        return json.loads(arq.read_text("utf-8"))

    host = urlparse(url).hostname or ""
    lim = LIMITADORES.get(host, PADRAO)
    falhas, n429, inicio_429 = 0, 0, None
    while True:
        if PARAR.is_set():
            raise Interrompido("consulta interrompida")
        lim.aguardar()
        try:
            r = _http(metodo, url, headers=headers, params=params, json_body=json_body, timeout=timeout)
        except ErroRede as e:
            falhas += 1
            if falhas >= tentativas:
                raise ErroFonte(f"{lim.nome}: falha de rede ({type(e).__name__})") from e
            dormir(2.0 * 2 ** (falhas - 1))
            continue

        if r.status_code == 429:
            n429 += 1
            inicio_429 = inicio_429 or time.monotonic()
            if time.monotonic() - inicio_429 > MAX_ESPERA_429:
                raise ErroFonte(f"{lim.nome}: limite de requisições persistiu por mais de 15 min")
            espera = _tempo_espera(r, n429)
            lim.limitado(espera)
            continue
        if r.status_code in (500, 502, 503, 504):
            falhas += 1
            if falhas >= tentativas:
                raise ErroFonte(f"{lim.nome}: {_resumo_erro(r)}")
            dormir(3.0 * 2 ** (falhas - 1))
            continue
        if r.status_code == 404:
            return {}
        if r.status_code >= 400:
            raise ErroFonte(f"{lim.nome}: {_resumo_erro(r)}")

        lim.sucesso(r)
        try:
            dados = r.json() if r.content else {}
        except ValueError as e:
            raise ErroFonte(f"{lim.nome}: resposta inválida (não é JSON)") from e
        if cache_horas:
            CACHE_DIR.mkdir(exist_ok=True)
            arq.write_text(json.dumps(dados, ensure_ascii=False), "utf-8")
        return dados
