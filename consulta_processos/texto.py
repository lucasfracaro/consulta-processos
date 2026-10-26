"""Limpeza do texto das publicações: alguns tribunais enviam ao DJEN o conteúdo em HTML."""
from __future__ import annotations

import re
from html.parser import HTMLParser

PARAGRAFOS = {"p", "div", "section", "article", "header", "footer", "table", "ul", "ol", "h1", "h2", "h3",
              "h4", "h5", "h6", "blockquote", "pre", "center"}
LINHAS = {"tr", "li"}
IGNORAR = {"style", "script", "head", "title"}


class _Extrator(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)   # converte &atilde; &ordm; &nbsp; etc.
        self.partes: list[str] = []
        self.ignorando = 0

    def handle_starttag(self, tag, attrs):
        if tag in IGNORAR:
            self.ignorando += 1
        elif tag in PARAGRAFOS:
            self.partes.append("\n")
        elif tag in ("br", "hr"):
            self.partes.append("\n")
        elif tag == "td":
            self.partes.append(" ")

    def handle_endtag(self, tag):
        if tag in IGNORAR:
            self.ignorando = max(0, self.ignorando - 1)
        elif tag in PARAGRAFOS:
            self.partes.append("\n\n")
        elif tag in LINHAS:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self.ignorando:
            self.partes.append(data.replace("\n", " "))


def parece_html(s: str) -> bool:
    return bool(s) and bool(re.search(r"<\s*(html|body|p|div|section|table|br|b|span)\b", s[:3000], re.I))


def limpar(texto: str | None) -> str:
    """Converte HTML em texto legível (parágrafos preservados); texto comum só é normalizado."""
    if not texto:
        return ""
    if parece_html(texto):
        ex = _Extrator()
        try:
            ex.feed(texto); ex.close()
            texto = "".join(ex.partes)
        except Exception:  # noqa: BLE001 — HTML quebrado: remove as etiquetas na marra
            texto = re.sub(r"<[^>]+>", " ", texto)
    else:
        import html as _h
        texto = _h.unescape(texto)
    texto = texto.replace("\xa0", " ")
    linhas = [re.sub(r"[ \t]+", " ", l).strip() for l in texto.splitlines()]
    saida, vazio = [], False
    for l in linhas:
        if not l:
            if saida and not vazio:
                saida.append("")
            vazio = True
            continue
        saida.append(l); vazio = False
    return "\n".join(saida).strip()


PAPEIS = r"(AUTORA?|R[ÉE]U?|R[ÉE]|REQUERENTE|REQUERID[OA]|EXEQUENTE|EXECUTAD[OA]|AGRAVANTE|AGRAVAD[OA]|APELANTE|APELAD[OA]|" \
         r"EMBARGANTE|EMBARGAD[OA]|RECLAMANTE|RECLAMAD[OA]|IMPETRANTE|IMPETRAD[OA]|RECORRENTE|RECORRID[OA]|" \
         r"INTERESSAD[OA]|TERCEIRO INTERESSADO|DENUNCIAD[OA]|ACUSAD[OA]|V[ÍI]TIMA|CREDOR|DEVEDOR|ADVOGAD[OA]\(?A?\)?)"
RE_PAPEL = re.compile(rf"^\s*{PAPEIS}\s*:\s*(.+?)\s*$", re.I | re.M)


def papeis(texto: str) -> list[tuple[str, str]]:
    """Extrai pares (papel, nome) de linhas como 'AGRAVANTE : FULANO' do texto limpo."""
    return [(m.group(1).upper(), m.group(2).strip(" :")) for m in RE_PAPEL.finditer(texto or "")]
