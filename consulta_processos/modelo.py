"""Modelo unificado de processo, independente da fonte."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Advogado:
    nome: str | None = None
    oab: list[str] = field(default_factory=list)


@dataclass
class Parte:
    nome: str | None
    polo: str | None = None              # ATIVO | PASSIVO | TERCEIRO | ADVOGADO | ...
    tipo: str | None = None              # Autor, Réu, Reclamante, Exequente...
    tipo_pessoa: str | None = None       # FISICA | JURIDICA
    documento: str | None = None
    advogados: list[Advogado] = field(default_factory=list)


@dataclass
class Movimentacao:
    data: str | None
    descricao: str | None
    codigo: str | None = None
    complemento: str | None = None
    fonte: str | None = None


@dataclass
class Processo:
    numero_cnj: str
    tribunal: str | None = None
    grau: str | None = None
    classe: str | None = None
    classe_codigo: str | None = None
    assuntos: list[str] = field(default_factory=list)
    area: str | None = None
    orgao_julgador: str | None = None
    comarca_uf: str | None = None
    sistema: str | None = None
    formato: str | None = None           # Eletrônico | Físico
    data_distribuicao: str | None = None
    data_arquivamento: str | None = None
    data_ultima_movimentacao: str | None = None
    valor_causa: float | None = None
    situacao: str | None = None          # ATIVO | INATIVO | ARQUIVADO | texto do tribunal
    segredo_justica: bool | None = None
    polo_do_consultado: str | None = None  # ATIVO = movido por ele | PASSIVO = contra ele
    papel_do_consultado: str | None = None
    como_foi_encontrado: str | None = None
    titulo_polo_ativo: str | None = None
    titulo_polo_passivo: str | None = None
    # análise (analise.py)
    ramo: str | None = None
    ano_ajuizamento: str | None = None
    natureza: str | None = None
    fase: str | None = None
    status_andamento: str | None = None
    resultado: str | None = None
    marcos: list[str] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    # leitura em linguagem simples (analise.py)
    resumo_simples: str | None = None
    explicacao_classe: str | None = None
    parte_contraria: list[str] = field(default_factory=list)
    ultimo_ato: dict[str, Any] = field(default_factory=dict)
    # quem do grupo investigado está no processo (a própria empresa/pessoa ou um sócio)
    envolvido: str | None = None
    instancias: list[str] = field(default_factory=list)
    processos_citados: list[str] = field(default_factory=list)
    partes: list[Parte] = field(default_factory=list)
    movimentacoes: list[Movimentacao] = field(default_factory=list)
    informacoes_extras: dict[str, Any] = field(default_factory=dict)
    publicacoes: list[dict[str, Any]] = field(default_factory=list)  # DJEN: intimações/editais com texto
    links: list[str] = field(default_factory=list)
    fontes: list[str] = field(default_factory=list)  # proveniência: escavador, judit, datajud

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # -- fusão de dados vindos de várias fontes
    def mesclar(self, outro: "Processo") -> None:
        for campo in (
            "tribunal", "grau", "classe", "classe_codigo", "area", "orgao_julgador", "comarca_uf",
            "sistema", "formato", "data_distribuicao", "data_arquivamento", "valor_causa",
            "situacao", "segredo_justica", "polo_do_consultado", "papel_do_consultado",
            "como_foi_encontrado", "titulo_polo_ativo", "titulo_polo_passivo",
        ):
            if getattr(self, campo) in (None, "", []) and getattr(outro, campo) not in (None, "", []):
                setattr(self, campo, getattr(outro, campo))
        datas = [d for d in (self.data_ultima_movimentacao, outro.data_ultima_movimentacao) if d]
        self.data_ultima_movimentacao = max(datas) if datas else None
        self.assuntos = list(dict.fromkeys(self.assuntos + outro.assuntos))
        if not self.partes or len(outro.partes) > len(self.partes):
            self.partes = outro.partes or self.partes
        vistos = {(m.data, (m.descricao or "")[:80]) for m in self.movimentacoes}
        for m in outro.movimentacoes:
            if (m.data, (m.descricao or "")[:80]) not in vistos:
                self.movimentacoes.append(m)
        self.movimentacoes.sort(key=lambda m: m.data or "", reverse=True)
        ids = {pub.get("id") for pub in self.publicacoes}
        self.publicacoes += [pub for pub in outro.publicacoes if pub.get("id") not in ids]
        self.publicacoes.sort(key=lambda pub: pub.get("data") or "", reverse=True)
        forca = {"DOCUMENTO_NO_TEXTO": 5, "CPF": 5, "CNPJ": 5, "DOCUMENTO_TRIBUNAL": 5,
                 "NOME_EXATO": 2, "NOME": 2, "NOME_SEMELHANTE": 1, "HOMONIMO": 0}
        if forca.get(outro.como_foi_encontrado, 0) > forca.get(self.como_foi_encontrado, 0):
            self.como_foi_encontrado = outro.como_foi_encontrado
        self.informacoes_extras.update(outro.informacoes_extras)
        self.links = list(dict.fromkeys(self.links + outro.links))
        self.instancias = list(dict.fromkeys(self.instancias + outro.instancias))
        self.processos_citados = list(dict.fromkeys(self.processos_citados + outro.processos_citados))
        self.envolvido = self.envolvido or outro.envolvido
        self.fontes = list(dict.fromkeys(self.fontes + outro.fontes))
