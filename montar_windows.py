"""Monta o pacote portátil para Windows: ConsultaProcessos-Windows.zip

    python3 montar_windows.py                     (baixa o Python oficial da python.org)
    python3 montar_windows.py --python-zip ARQ    (usa um python-3.x-embed-amd64.zip já baixado)

Nada é compilado. O pacote contém:
  Consulta de Processos.exe   lançador (o mesmo tipo de .exe que o pip usa no Windows), que abre o Python da pasta
  python\\                     Python oficial portátil ("embeddable") da python.org — não precisa instalar
  consulta_processos\\         o programa

No outro computador: extrair o zip em qualquer pasta e dar dois cliques em "Consulta de Processos.exe".
Roda em qualquer computador com Linux, Mac ou Windows que tenha Python 3.9+; só a biblioteca padrão é usada.
"""
from __future__ import annotations

import argparse
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

PYTHON_VERSAO = "3.12.10"
URL_PYTHON = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"
RAIZ = Path(__file__).resolve().parent
NOME_EXE = "Consulta de Processos.exe"
# <launcher_dir> é trocado pelo lançador pela pasta onde o .exe está: o pacote funciona em qualquer lugar.
SHEBANG = b"#!<launcher_dir>\\python\\pythonw.exe -m consulta_processos.gui\n"

LEIA_ME = """CONSULTA DE PROCESSOS — versão portátil para Windows
=====================================================

Como usar
  1. Extraia este zip numa pasta (ex.: Documentos\\Consulta de Processos). Não rode de dentro do zip.
  2. Dê dois cliques em "Consulta de Processos.exe". A tela abre no navegador.
  3. Na aba "Fontes e chaves", cadastre a chave gratuita do Portal da Transparência (e outras, se tiver).

Não precisa instalar Python nem nada: o Python oficial (python.org) já está na pasta "python".
As chaves ficam em config.json e os relatórios na pasta "relatorios", dentro desta pasta.

Se o Windows mostrar "O Windows protegeu o computador": clique em "Mais informações" > "Executar assim mesmo".
(O .exe é o lançador padrão usado pelo pip; ele não é assinado digitalmente, por isso o aviso.)

Se nada abrir: use "Diagnostico.bat" — ele abre a tela com uma janela de mensagens, útil para ver erros.
Para levar para outro computador: copie a pasta inteira (ou o zip).
"""

DIAGNOSTICO = (
    "@echo off\r\n"
    "cd /d \"%~dp0\"\r\n"
    "echo Abrindo a Consulta de Processos com mensagens de diagnostico...\r\n"
    "\"%~dp0python\\python.exe\" -m consulta_processos.gui\r\n"
    "echo.\r\necho O programa foi encerrado. Se houve erro, ele aparece acima.\r\npause\r\n"
)


def baixar_python(destino: Path) -> Path:
    url = URL_PYTHON.format(v=PYTHON_VERSAO)
    print(f"Baixando o Python {PYTHON_VERSAO} oficial para Windows: {url}")
    with urllib.request.urlopen(url, timeout=120) as r:
        dados = r.read()
    if len(dados) < 5_000_000:
        raise SystemExit("Download incompleto do Python. Tente de novo ou use --python-zip.")
    destino.write_bytes(dados)
    print(f"  ok ({len(dados) / 1e6:.1f} MB)")
    return destino


def criar_exe(destino: Path) -> None:
    """Lançador + linha #! + zip mínimo (é assim que o pip monta os .exe de programas no Windows)."""
    lancador = (RAIZ / "consulta_processos" / "windows" / "lancador-w64.exe").read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("__main__.py", "import consulta_processos.gui as g\ng.main()\n")
    destino.write_bytes(lancador + SHEBANG + buf.getvalue())


def configurar_python(pasta_python: Path) -> None:
    pths = list(pasta_python.glob("python3*._pth"))
    if not pths:
        raise SystemExit("Arquivo ._pth não encontrado: o zip informado não parece ser o Python 'embeddable'.")
    pth = pths[0]
    zip_std = pth.stem + ".zip"   # ex.: python312.zip
    # caminhos relativos à pasta python\: biblioteca padrão, a própria pasta e a pasta do programa (..)
    pth.write_text(f"{zip_std}\n.\n..\n", encoding="utf-8")


def copiar_programa(destino: Path) -> None:
    shutil.copytree(RAIZ / "consulta_processos", destino / "consulta_processos",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "windows"))
    for extra in ("README.md",):
        if (RAIZ / extra).exists():
            shutil.copy2(RAIZ / extra, destino / extra)
    (destino / "LEIA-ME.txt").write_text(LEIA_ME, encoding="utf-8-sig")
    (destino / "Diagnostico.bat").write_text(DIAGNOSTICO, encoding="ascii")
    shutil.copy2(RAIZ / "consulta_processos" / "windows" / "LICENSE-distlib.txt", destino / "LICENSE-lancador.txt")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Monta o pacote portátil da Consulta de Processos para Windows.")
    ap.add_argument("--python-zip", help="python-3.x-embed-amd64.zip já baixado (evita o download)")
    ap.add_argument("--saida", default=str(RAIZ / "dist"), help="pasta onde o zip final será criado")
    a = ap.parse_args(argv)

    saida = Path(a.saida); saida.mkdir(parents=True, exist_ok=True)
    trabalho = saida / "_montagem"
    if trabalho.exists():
        shutil.rmtree(trabalho)
    pacote = trabalho / "Consulta de Processos"
    pacote.mkdir(parents=True)

    zip_python = Path(a.python_zip) if a.python_zip else baixar_python(trabalho / "python-embed.zip")
    print("Extraindo o Python portátil...")
    with zipfile.ZipFile(zip_python) as z:
        z.extractall(pacote / "python")
    if not (pacote / "python" / "pythonw.exe").exists():
        raise SystemExit("pythonw.exe não encontrado: o zip informado não é o Python 'embeddable' para Windows.")
    configurar_python(pacote / "python")
    print("Copiando o programa e criando o lançador...")
    copiar_programa(pacote)
    criar_exe(pacote / NOME_EXE)

    final = saida / "ConsultaProcessos-Windows.zip"
    if final.exists():
        final.unlink()
    with zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arq in sorted(pacote.rglob("*")):
            if arq.is_file():
                z.write(arq, arq.relative_to(trabalho))
    shutil.rmtree(trabalho)
    print(f"\nPronto: {final}  ({final.stat().st_size / 1e6:.1f} MB)")
    print("Copie esse zip para o computador Windows, extraia e dê dois cliques em 'Consulta de Processos.exe'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
