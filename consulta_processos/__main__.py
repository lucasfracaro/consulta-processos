"""Dossiê judicial a partir só do CPF ou CNPJ, com fontes oficiais gratuitas.

    python -m consulta_processos 11.222.333/0001-81
    python -m consulta_processos 529.982.247-25

Fluxo automático:
  1. identidade   CNPJ → Receita (razão social, fantasia, sócios PF e PJ)
                  CPF  → Portal da Transparência (nome)
  2. descoberta   DJEN pelo nome de cada alvo (empresa, sócios, sócios PJ), com confirmação por documento
  3. expansão     processos citados nas publicações são verificados e incluídos se o alvo for parte
  4. detalhe      DataJud (capa + movimentos de cada instância) e histórico completo do DJEN
  5. análise      natureza, fase, status do andamento, resultado, alertas
  6. restrições   CEIS/CNEP, certidões TCU/CNJ, dívida ativa PGFN
O relatório HTML é regravado ao fim de cada etapa e se atualiza sozinho enquanto a consulta roda.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .analise import analisar
from .linguagem import explicar, resumo_executivo
from .documento import Documento, cnj_valido, identificar, so_digitos
from . import config as cfgmod
from .fontes import catalogo, pgfn
from .fontes.datajud import DataJud
from .fontes.djen import DJEN, FORCA, Alvo
FORCA = {**FORCA, "DOCUMENTO_NA_FONTE": 3}
from .fontes.governo import Transparencia, certidoes_tcu, dados_cnpj
from .http import PARAR, ErroFonte, Interrompido
from .modelo import Processo
from .relatorio import nome_base, salvar

log = logging.getLogger("consulta")


def carregar_env(arquivo=".env"):
    p = Path(arquivo)
    if not p.exists():
        p = Path(__file__).resolve().parent.parent / ".env"  # pasta do programa
    if p.exists():
        for linha in p.read_text("utf-8").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Investigacao:
    def __init__(self, a: argparse.Namespace, doc: Documento):
        self.a, self.doc = a, doc
        self.processos: dict[str, Processo] = {}
        self.extras: dict = {"erros_por_fonte": {}, "limites_da_consulta": [], "alvos": []}
        self.consultadas: list[str] = []
        self.nome: str | None = a.nome
        self.saida = Path(a.saida)
        self.base = nome_base(doc.formatado)
        self.djen = DJEN(anos_janela=a.anos)
        self._cancelado = False
        PARAR.clear()
        self.djen.deve_parar = lambda: self.cancelado
        self.cfg = cfgmod.carregar()
        self.transp = (Transparencia(catalogo.chave(self.cfg, "transparencia"))
                       if catalogo.pronta(self.cfg, "transparencia") else None)

    # ------------------------------------------------------------ utilidades
    @property
    def cancelado(self) -> bool:
        return self._cancelado

    @cancelado.setter
    def cancelado(self, valor: bool) -> None:
        self._cancelado = valor
        if valor:
            PARAR.set()   # acorda na hora qualquer espera em andamento

    def tentar(self, fonte: str, func, *args):
        try:
            r = func(*args)
            if fonte not in self.consultadas:
                self.consultadas.append(fonte)
            return r
        except Interrompido:
            return None
        except ErroFonte as e:
            self.extras["erros_por_fonte"][fonte] = str(e)
            log.error("%s falhou: %s", fonte, e)
            return None

    def juntar(self, p: Processo) -> bool:
        atual = self.processos.get(p.numero_cnj)
        if atual:
            if p.envolvido and atual.envolvido and p.envolvido not in atual.envolvido:
                atual.envolvido = f"{atual.envolvido}; {p.envolvido}"
            atual.mesclar(p)
            return False
        self.processos[p.numero_cnj] = p
        return True

    def analisar_todos(self) -> None:
        """Natureza, status, resumo etc. — roda em toda gravação, para o relatório parcial já vir analisado."""
        nomes = {}
        for a in self.extras.get("alvos", []):
            nomes.setdefault(a["quem"], a["nome"])
        for p in self.processos.values():
            analisar(p)
            explicar(p, nomes.get((p.envolvido or "").split(";")[0].strip(), self.nome))
        self.extras["resumo_executivo"] = resumo_executivo(list(self.processos.values()), self.nome)

    def gravar(self, etapa: str, final: bool = False):
        self.etapa = etapa
        self.analisar_todos()
        lista = sorted(self.processos.values(),
                       key=lambda p: (len(p.alertas), FORCA.get(p.como_foi_encontrado, 0), p.data_ultima_movimentacao or ""),
                       reverse=True)
        consulta = {"tipo": self.doc.tipo, "documento": self.doc.formatado, "nome": self.nome,
                    "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "fontes_consultadas": self.consultadas, "etapa": etapa, "em_andamento": not final}
        return salvar(self.saida, consulta, lista, self.extras, base=self.base)

    # ------------------------------------------------------------ 1. identidade e alvos
    def identificar_alvos(self) -> list[Alvo]:
        doc, alvos = self.doc, []
        if doc.tipo == "CNPJ":
            cad = dados_cnpj(doc) if catalogo.pronta(self.cfg, "receita") else {}
            if cad:
                self.consultadas.append("receita (dados abertos CNPJ)")
            socios = cad.pop("_socios", [])
            self.extras["dados_cadastrais"] = cad
            self.nome = self.nome or cad.get("razao_social")
            if self.nome:
                alvos.append(Alvo("Empresa", self.nome, doc.valor, "CNPJ"))
            if cad.get("nome_fantasia") and cad["nome_fantasia"].upper() != (self.nome or "").upper():
                alvos.append(Alvo("Empresa", cad["nome_fantasia"], doc.valor, "CNPJ"))
            if self.a.sem_socios:
                socios = []
            for s in socios:
                if s["tipo"] == "CNPJ" and len(s["documento"]) == 14:
                    sub = dados_cnpj(Documento("CNPJ", s["documento"], s["documento"]))
                    nome_pj = sub.get("razao_social") or s["nome"]
                    alvos.append(Alvo(f"Sócio PJ: {nome_pj}", nome_pj, s["documento"], "CNPJ"))
                    self.extras.setdefault("socios_pj", {})[nome_pj] = {k: v for k, v in sub.items() if not k.startswith("_")}
                else:
                    alvos.append(Alvo(f"Sócio: {s['nome']}", s["nome"], s["documento"] or None, "CPF"))
        elif doc.tipo == "CPF":
            if not self.nome and self.transp:
                info = self.tentar("transparencia (pessoa física)", self.transp.pessoa, doc) or {}
                self.nome = info.get("nome")
                if info.get("indicadores"):
                    self.extras["vinculos_federais"] = info["indicadores"]
            if self.nome:
                alvos.append(Alvo("Pessoa", self.nome, doc.valor, "CPF"))
            else:
                self.extras["limites_da_consulta"].append(
                    "Não foi possível obter o nome do CPF: "
                    + ("ele não consta nas bases federais do Portal da Transparência." if self.transp
                       else "cadastre a chave gratuita do Portal da Transparência em 'Fontes e chaves' para descobrir o nome automaticamente.")
                    + " Sem nome, a busca de processos no DJEN não é possível; rode novamente com --nome.")
        else:  # RG
            self.extras["limites_da_consulta"].append(
                "Nenhum tribunal indexa processos por RG; a busca usa o nome informado em --nome.")
            if self.nome:
                alvos.append(Alvo("Pessoa", self.nome, None, "CPF"))
        self.extras["alvos"] = [{"quem": a.rotulo, "nome": a.nome, "documento": a.documento} for a in alvos]
        return alvos

    # ------------------------------------------------------------ pipeline
    def executar(self) -> dict:
        alvos = self.identificar_alvos()
        for a in alvos:
            log.info("Alvo: %s (%s)", a.rotulo, a.nome)
        self.gravar("identificação concluída")

        # 2. descoberta
        # 2a. fontes pagas/personalizadas: buscam direto pelo CPF/CNPJ do consultado
        rotulo_principal = alvos[0].rotulo if alvos else ("Empresa" if self.doc.tipo == "CNPJ" else "Pessoa")
        for fonte in catalogo.buscadores_pagos_e_personalizados(self.cfg):
            if self.cancelado:
                break
            log.info("%s: buscando processos pelo documento...", fonte.nome)
            achados = self.tentar(fonte.nome, fonte.buscar, self.doc, rotulo_principal) or []
            novos = sum(self.juntar(p) for p in achados)
            log.info("  %s: %d processos (%d novos)", fonte.nome, len(achados), novos)
            self.gravar(f"buscando processos ({fonte.nome})")
        # 2b. DJEN (oficial): pelo nome de cada alvo
        if catalogo.pronta(self.cfg, "djen"):
            for alvo in alvos:
                if self.cancelado:
                    break
                log.info("DJEN: buscando publicações de %s...", alvo.rotulo)
                for p in self.tentar("djen", self.djen.buscar, alvo) or []:
                    self.juntar(p)
                self.gravar(f"buscando processos ({alvo.rotulo})")
        log.info("Encontrados %d processos.", len(self.processos))

        # 3. expansão por processos citados
        verificados: set[str] = set(self.processos)
        usar_djen = catalogo.pronta(self.cfg, "djen")
        for rodada in range(0 if (self.cancelado or not usar_djen) else self.a.profundidade):
            candidatos = [n for p in list(self.processos.values()) for n in p.processos_citados if n not in verificados]
            candidatos = list(dict.fromkeys(candidatos))[: self.a.max_citados]
            if not candidatos:
                break
            log.info("Verificando %d processos citados nas publicações (rodada %d)...", len(candidatos), rodada + 1)
            novos, pendentes = 0, []
            for tentativa, lista in enumerate((candidatos, None)):
                if tentativa == 1:
                    if not pendentes or self.cancelado:
                        break
                    log.info("  Tentando de novo %d processos que falharam...", len(pendentes))
                    lista, pendentes = pendentes, []
                for n in lista:
                    if self.cancelado:
                        break
                    verificados.add(n)
                    try:
                        p = self.djen.verificar_numero(n, alvos)
                    except Interrompido:
                        break
                    except ErroFonte as e:
                        log.warning("  %s: %s", n, e)
                        pendentes.append(n)
                        continue
                    if p:
                        p.como_foi_encontrado = p.como_foi_encontrado or "CITADO_EM_PUBLICACAO"
                        p.informacoes_extras["Descoberto por"] = "citado em publicação de outro processo"
                        novos += self.juntar(p)
            if pendentes:
                self.extras["erros_por_fonte"]["djen (processos citados)"] = (
                    f"{len(pendentes)} processo(s) citado(s) não puderam ser verificados: "
                    + ", ".join(pendentes[:10]) + ("…" if len(pendentes) > 10 else ""))
            log.info("  %d novos.", novos)
            self.gravar("verificando processos citados")

        if self.a.somente_confirmados:
            self.processos = {k: v for k, v in self.processos.items() if FORCA.get(v.como_foi_encontrado, 0) >= 3}

        # 4. detalhe oficial
        if self.processos:
            log.info("DataJud/CNJ: detalhando %d processos...", len(self.processos))
            if catalogo.pronta(self.cfg, "datajud"):
                DataJud(catalogo.chave(self.cfg, "datajud")).enriquecer(self.processos)
            self.consultadas.append("datajud")
            self.gravar("detalhando no DataJud")
            log.info("DJEN: histórico completo de publicações...")
            for i, p in enumerate(self.processos.values() if usar_djen else [], 1):
                if self.cancelado:
                    break
                self.tentar("djen", self.djen.completar, p)
                if i % 20 == 0:
                    log.info("  publicações: %d de %d processos", i, len(self.processos))
                    self.gravar(f"baixando publicações ({i}/{len(self.processos)})")

        # 5. análise (também roda a cada gravação parcial)
        self.gravar("análise concluída")

        # 6. restrições e dívidas
        PARAR.clear()  # as checagens finais são rápidas; rodam mesmo após 'parar'
        self.restricoes()
        self.extras["limites_da_consulta"] += [
            "O DJEN só mostra processos com publicação desde a adesão do tribunal (obrigatório desde 2025); "
            "processos antigos sem intimação recente podem faltar.",
            "Processos em segredo de justiça não aparecem em fontes abertas.",
            "Valor da causa e a lista completa de partes com CPF/CNPJ não são publicados pelo DataJud.",
            "Sócios pessoa física têm CPF mascarado na Receita: a confirmação usa os dígitos visíveis.",
        ]
        if self.cancelado:
            self.extras["limites_da_consulta"].insert(
                0, "A consulta foi interrompida a pedido: o relatório mostra só o que foi encontrado até aquele momento.")
        invalidos = [n for n in self.processos if not cnj_valido(n)]
        if invalidos:
            self.extras["numeros_fora_do_padrao_cnj"] = invalidos
        return self.gravar("concluída", final=True)

    def restricoes(self):
        doc = self.doc
        if self.transp:
            r = self.tentar("transparencia (CEIS/CNEP)", self.transp.sancoes, doc)
            if r is not None:
                self.extras["sancoes_administrativas"] = r
            if doc.tipo == "CNPJ":
                info = self.tentar("transparencia (pessoa jurídica)", self.transp.pessoa, doc) or {}
                if info.get("indicadores"):
                    self.extras["vinculos_federais"] = info["indicadores"]
            for s in self.extras.get("alvos", []):
                if s["quem"].startswith("Sócio PJ") and s["documento"]:
                    r = self.tentar("transparencia (CEIS/CNEP)", self.transp.sancoes,
                                    Documento("CNPJ", s["documento"], s["documento"]))
                    if r:
                        self.extras.setdefault("sancoes_administrativas", []).extend(
                            dict(x, sancionado=s["nome"]) for x in r)
        else:
            self.extras["limites_da_consulta"].append("CEIS/CNEP e nome por CPF exigem a chave gratuita do Portal da Transparência ('Fontes e chaves').")
        cnpjs = [doc.valor] if doc.tipo == "CNPJ" else []
        cnpjs += [s["documento"] for s in self.extras.get("alvos", []) if s["quem"].startswith("Sócio PJ")]
        for c in dict.fromkeys(cnpjs if catalogo.pronta(self.cfg, "tcu") else []):
            r = self.tentar("tcu (certidões consolidadas)", certidoes_tcu, Documento("CNPJ", c, c))
            if r and r.get("certidoes"):
                self.extras.setdefault("certidoes_tcu", []).extend(
                    dict(x, empresa=r.get("razao_social") or c) for x in r["certidoes"])
        pasta_pgfn = self.a.pgfn_dir or (catalogo.extra(self.cfg, "pgfn", "pasta") if catalogo.pronta(self.cfg, "pgfn") else None)
        if pasta_pgfn:
            pasta_pgfn = os.path.expanduser(pasta_pgfn)
            log.info("PGFN: varrendo dados abertos em %s...", pasta_pgfn)
            r = pgfn.buscar(pasta_pgfn, doc, self.nome)
            if r:
                if doc.tipo == "CPF" and not self.nome:
                    r["observacao"] = "CPF mascarado na base e nome desconhecido: pode haver registros de outras pessoas."
                self.extras["divida_ativa_uniao"] = r
                self.consultadas.append("pgfn (dados abertos)")
        else:
            self.extras["limites_da_consulta"].append(
                "Dívida ativa da União (PGFN) não consultada: baixe os dados abertos e informe a pasta em 'Fontes e chaves'.")


def criar_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="consulta_processos",
                                 description="Dossiê judicial por CPF/CNPJ com fontes oficiais gratuitas.")
    ap.add_argument("documento")
    ap.add_argument("--tipo", choices=["cpf", "cnpj", "rg"], help="força o tipo (por padrão é detectado)")
    ap.add_argument("--nome", help="opcional: sobrescreve o nome descoberto automaticamente (obrigatório só para RG)")
    ap.add_argument("--sem-socios", action="store_true", help="CNPJ: não investiga os sócios")
    ap.add_argument("--profundidade", type=int, default=2, help="rodadas de verificação de processos citados (padrão 2)")
    ap.add_argument("--max-citados", type=int, default=80, help="máximo de citados verificados por rodada")
    ap.add_argument("--somente-confirmados", action="store_true", help="só processos com CPF/CNPJ no texto da publicação")
    ap.add_argument("--pgfn-dir", default=None, help="pasta com os CSV/ZIP da PGFN (por padrão, a configurada na tela)")
    ap.add_argument("--anos", type=int, default=6, help="janela do DJEN, se a API exigir período")
    ap.add_argument("--saida", default="relatorios")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv=None) -> int:
    carregar_env()
    a = criar_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(message)s")

    try:
        doc = identificar(a.documento, a.tipo)
    except ValueError as e:
        log.error(e); return 2
    log.info("Documento: %s %s", doc.tipo, doc.formatado)

    inv = Investigacao(a, doc)
    log.info("Relatório (atualiza durante a consulta): %s", (inv.saida / f"{inv.base}.html").resolve())
    arquivos = inv.executar()

    lista = list(inv.processos.values())
    ativos = sum(1 for p in lista if p.polo_do_consultado == "ATIVO")
    passivos = sum(1 for p in lista if p.polo_do_consultado == "PASSIVO")
    com_alerta = sum(1 for p in lista if p.alertas)
    print(f"\n{inv.nome or doc.formatado}: {len(lista)} processos | movidos por: {ativos} | contra: {passivos} | "
          f"com alerta: {com_alerta}")
    for k, v in arquivos.items():
        print(f"  {k.upper():5} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
