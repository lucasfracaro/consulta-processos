"""Configuração local do usuário: chaves de API, fontes ativadas e fontes personalizadas.

Fica em config.json, na pasta do programa, com permissão só para o dono do arquivo. Nada disso vai
para o código. Na primeira execução, chaves antigas do .env são importadas automaticamente.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

PASTA = Path(__file__).resolve().parent.parent
ARQUIVO = PASTA / "config.json"
_TRAVA = threading.Lock()

PADRAO = {
    "fontes": {},          # id -> {"ativa": bool, "chave": str, "extra": {...}}
    "personalizadas": [],  # definições de fontes adicionadas pelo assistente
}

# variáveis antigas do .env -> (fonte, campo)
MIGRAR_ENV = {
    "TRANSPARENCIA_API_KEY": ("transparencia", "chave"),
    "ESCAVADOR_TOKEN": ("escavador", "chave"),
    "JUDIT_API_KEY": ("judit", "chave"),
    "DATAJUD_API_KEY": ("datajud", "chave"),
    "PGFN_DIR": ("pgfn", "pasta"),
}


def _ler_env() -> dict[str, str]:
    env = PASTA / ".env"
    vals = {}
    if env.exists():
        for linha in env.read_text("utf-8").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    vals[k.strip()] = v
    return vals


def carregar() -> dict:
    with _TRAVA:
        if ARQUIVO.exists():
            try:
                cfg = json.loads(ARQUIVO.read_text("utf-8"))
            except ValueError:
                cfg = json.loads(json.dumps(PADRAO))
        else:
            cfg = json.loads(json.dumps(PADRAO))
        for k, v in PADRAO.items():
            cfg.setdefault(k, json.loads(json.dumps(v)))
        # migração do .env (só preenche o que ainda não existe)
        mudou = False
        for var, (fonte, campo) in MIGRAR_ENV.items():
            valor = os.getenv(var) or _ler_env().get(var)
            f = cfg["fontes"].setdefault(fonte, {})
            if valor and not (f.get(campo) or f.get("extra", {}).get(campo)):
                if campo == "chave":
                    f["chave"] = valor
                else:
                    f.setdefault("extra", {})[campo] = valor
                f.setdefault("ativa", True)
                mudou = True
        if mudou or not ARQUIVO.exists():
            _salvar_sem_trava(cfg)
        return cfg


def _salvar_sem_trava(cfg: dict) -> None:
    tmp = ARQUIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    try:
        os.chmod(tmp, 0o600)  # só o dono lê (onde o sistema permitir)
    except OSError:
        pass
    tmp.replace(ARQUIVO)


def salvar(cfg: dict) -> None:
    with _TRAVA:
        _salvar_sem_trava(cfg)


def fonte(cfg: dict, fid: str) -> dict:
    return cfg["fontes"].setdefault(fid, {})


def mascarar(chave: str | None) -> str:
    if not chave:
        return ""
    return "•" * 6 + chave[-4:] if len(chave) > 6 else "•" * len(chave)
