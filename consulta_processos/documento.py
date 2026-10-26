"""Validação e identificação de CPF, CNPJ (numérico e alfanumérico), RG e número CNJ."""
from __future__ import annotations

import re
from dataclasses import dataclass

UF_POR_TR = {
    "01": "ac", "02": "al", "03": "ap", "04": "am", "05": "ba", "06": "ce", "07": "dft",
    "08": "es", "09": "go", "10": "ma", "11": "mt", "12": "ms", "13": "mg", "14": "pa",
    "15": "pb", "16": "pr", "17": "pe", "18": "pi", "19": "rj", "20": "rn", "21": "rs",
    "22": "ro", "23": "rr", "24": "sc", "25": "se", "26": "sp", "27": "to",
}


def so_digitos(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def normalizar_cnpj(s: str) -> str:
    """Remove pontuação; mantém letras (CNPJ alfanumérico, em vigor desde jul/2026)."""
    return re.sub(r"[^0-9A-Za-z]", "", s or "").upper()


def cpf_valido(cpf: str) -> bool:
    c = so_digitos(cpf)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(c[j]) * ((i + 1) - j) for j in range(i))
        if (soma * 10) % 11 % 10 != int(c[i]):
            return False
    return True


def cnpj_valido(cnpj: str) -> bool:
    c = normalizar_cnpj(cnpj)
    if len(c) != 14 or not re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", c) or c == c[0] * 14:
        return False
    valores = [ord(ch) - 48 for ch in c[:12]]  # regra da Receita: valor ASCII - 48
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    d1 = sum(v * p for v, p in zip(valores, pesos1)) % 11
    d1 = 0 if d1 < 2 else 11 - d1
    d2 = sum(v * p for v, p in zip(valores + [d1], pesos2)) % 11
    d2 = 0 if d2 < 2 else 11 - d2
    return c[12:] == f"{d1}{d2}"


@dataclass
class Documento:
    tipo: str          # CPF | CNPJ | RG
    valor: str         # normalizado
    original: str

    @property
    def formatado(self) -> str:
        v = self.valor
        if self.tipo == "CPF":
            return f"{v[:3]}.{v[3:6]}.{v[6:9]}-{v[9:]}"
        if self.tipo == "CNPJ":
            return f"{v[:2]}.{v[2:5]}.{v[5:8]}/{v[8:12]}-{v[12:]}"
        return v


def identificar(entrada: str, tipo_forcado: str | None = None) -> Documento:
    tipo_forcado = (tipo_forcado or "").upper() or None
    if tipo_forcado == "RG":
        return Documento("RG", re.sub(r"[^0-9Xx]", "", entrada).upper(), entrada)
    cnpj = normalizar_cnpj(entrada)
    cpf = so_digitos(entrada)
    if tipo_forcado in (None, "CNPJ") and cnpj_valido(cnpj):
        return Documento("CNPJ", cnpj, entrada)
    if tipo_forcado in (None, "CPF") and len(cpf) == 11 and cpf_valido(cpf):
        return Documento("CPF", cpf, entrada)
    if tipo_forcado in ("CPF", "CNPJ"):
        raise ValueError(f"{tipo_forcado} inválido (dígito verificador não confere): {entrada}")
    # Sem DV válido de CPF/CNPJ: tratamos como RG (não há padrão nacional de validação de RG).
    return Documento("RG", re.sub(r"[^0-9Xx]", "", entrada).upper(), entrada)


# ---------------------------------------------------------------- número CNJ

def cnj_digitos(numero: str) -> str:
    return so_digitos(numero)


def cnj_formatado(numero: str) -> str:
    d = cnj_digitos(numero)
    if len(d) != 20:
        return numero
    return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:]}"


def cnj_valido(numero: str) -> bool:
    d = cnj_digitos(numero)
    if len(d) != 20:
        return False
    n, dv, ano, j, tr, oooo = d[:7], d[7:9], d[9:13], d[13], d[14:16], d[16:]
    resto = int(n + ano + j + tr + oooo + "00") % 97
    return int(dv) == 98 - resto


def alias_datajud(numero: str) -> str | None:
    """Converte o segmento J.TR do número CNJ no índice da API pública do DataJud."""
    d = cnj_digitos(numero)
    if len(d) != 20:
        return None
    j, tr = d[13], d[14:16]
    if j == "3":
        return "stj"
    if j == "4":
        return f"trf{int(tr)}" if tr != "90" else None
    if j == "5":
        return "tst" if tr == "00" else f"trt{int(tr)}"
    if j == "6":
        if tr == "00":
            return "tse"
        uf = UF_POR_TR.get(tr)
        return f"tre-{'df' if uf == 'dft' else uf}" if uf else None
    if j == "7":
        return "stm"
    if j == "8":
        uf = UF_POR_TR.get(tr)
        return f"tj{uf}" if uf else None
    if j == "9":
        return {"13": "tjmmg", "21": "tjmrs", "26": "tjmsp"}.get(tr)
    return None  # STF (1) e CNJ (2) não estão no DataJud público
