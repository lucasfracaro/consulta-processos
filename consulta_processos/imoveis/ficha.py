"""Ficha do imóvel: junta as transações de ITBI, calcula indicadores e gera o relatório HTML."""
from __future__ import annotations

import html
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

from . import itbi_sp

PASTA_REL = Path(__file__).resolve().parent.parent.parent / "relatorios"

CIDADES = {
    "sao_paulo": {"nome": "São Paulo (capital)", "portal": "https://prefeitura.sp.gov.br/web/fazenda/servicos/iptu",
                  "inscricao": "SQL — número do contribuinte, no carnê do IPTU (ex.: 000.000.0000-0)"},
    "santo_andre": {"nome": "Santo André", "portal": "https://www.santoandre.sp.gov.br"},
    "sao_bernardo": {"nome": "São Bernardo do Campo", "portal": "https://www.saobernardo.sp.gov.br"},
    "sao_caetano": {"nome": "São Caetano do Sul", "portal": "https://www.saocaetanodosul.sp.gov.br"},
    "diadema": {"nome": "Diadema", "portal": "https://www.diadema.sp.gov.br"},
    "maua": {"nome": "Mauá", "portal": "https://www.maua.sp.gov.br"},
    "ribeirao_pires": {"nome": "Ribeirão Pires", "portal": "https://www.ribeiraopires.sp.gov.br"},
    "rio_grande_da_serra": {"nome": "Rio Grande da Serra", "portal": "https://www.riograndedaserra.sp.gov.br"},
}

LINKS_SP = [
    ("Débitos de IPTU pelo SQL (Prefeitura de SP)", "http://www3.prefeitura.sp.gov.br/iptudeb3/Forms/iptudeb3_pag01.aspx"),
    ("Certidão Conjunta de Débitos de Tributos Imobiliários", "http://www3.prefeitura.sp.gov.br/certidaotributaria"),
    ("Valor venal de referência atual (ITBI)", "https://itbi.prefeitura.sp.gov.br/valorreferencia/tvm/frm_tvm_consulta_valor.aspx"),
]
LINK_MATRICULA = ("Pedir a matrícula no cartório (dono atual, penhoras, hipotecas, indisponibilidade)",
                  "https://www.registradores.org.br/")

LIMITES_SP = [
    "Só cobre a cidade de São Paulo: as planilhas abertas de ITBI são publicadas apenas pela Prefeitura da capital.",
    "Só aparecem imóveis que tiveram alguma transação com ITBI pago desde 2006 (compra e venda, por exemplo). "
    "Heranças e doações pagam outro imposto (ITCMD) e não entram; imóveis rurais também não.",
    "Não mostra nomes de donos, penhoras, hipotecas, alienação fiduciária nem indisponibilidade: isso só consta "
    "na matrícula, emitida pelo cartório de registro de imóveis (serviço pago).",
    "Os valores de transação são os declarados por quem pagou o imposto; o valor venal de referência é o da época de cada transação.",
]


def _f(v) -> float | None:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


def _moeda(v) -> str:
    v = _f(v)
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if v else "—"


def _num(v, casas=2) -> str:
    v = _f(v)
    if v is None:
        return "—"
    s = f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s.rstrip("0").rstrip(",") if casas else s


def mat_fmt(m) -> str:
    return f"{int(m):,}".replace(",", ".") if str(m or "").isdigit() else str(m or "")


def _data_br(d) -> str:
    return f"{d[8:10]}/{d[5:7]}/{d[:4]}" if d and re.match(r"\d{4}-\d{2}-\d{2}", str(d)) else (d or "—")


def montar(transacoes: list[dict], consulta: dict) -> dict:
    trans = sorted(transacoes, key=lambda r: r.get("data_transacao") or "", reverse=True)
    ficha = {"consulta": consulta, "encontrado": bool(trans), "transacoes": [], "imovel": {}, "indicadores": {},
             "alertas": [], "links": [], "limites": list(LIMITES_SP)}
    if trans:
        rec = trans[0]
        enderecos = " ".join(x for x in (rec.get("logradouro"), rec.get("numero")) if x)
        ficha["imovel"] = {
            "SQL (número do contribuinte)": itbi_sp.sql_formatado(rec.get("sql_n")),
            "Endereço": ", ".join(x for x in (enderecos, rec.get("complemento")) if x) or None,
            "Bairro": rec.get("bairro"), "CEP": rec.get("cep"), "Referência": rec.get("referencia"),
            "Uso": rec.get("uso_desc") or rec.get("uso"), "Padrão da construção": rec.get("padrao_desc") or rec.get("padrao"),
            "Ano de construção (corrigido, IPTU)": rec.get("acc"),
            "Área construída": f"{_num(rec.get('area_construida'))} m²" if _f(rec.get("area_construida")) else None,
            "Área do terreno": f"{_num(rec.get('area_terreno'))} m²" if _f(rec.get("area_terreno")) else None,
            "Fração ideal": rec.get("fracao_ideal"), "Testada": f"{_num(rec.get('testada'))} m" if _f(rec.get("testada")) else None,
            "Situação do SQL": rec.get("situacao_sql"),
            "Cartório / matrícula informados nas guias": ", ".join(sorted({
                f"{r['cartorio_n']}º RI — matrícula {mat_fmt(r['matricula_n'])}" for r in trans if r.get("cartorio_n") and r.get("matricula_n")})) or None,
        }
        for r in trans:
            area = _f(r.get("area_construida"))
            valor = _f(r.get("valor_transacao"))
            ficha["transacoes"].append({
                "data": r.get("data_transacao"), "natureza": r.get("natureza"),
                "valor_declarado": valor, "valor_venal_referencia": _f(r.get("valor_venal_ref")),
                "base_calculo": _f(r.get("base_calculo")), "proporcao": r.get("proporcao"),
                "financiamento": r.get("tipo_financiamento"), "valor_financiado": _f(r.get("valor_financiado")),
                "valor_m2": round(valor / area, 2) if valor and area else None,
                "cartorio": r.get("cartorio"), "matricula": r.get("matricula"),
            })
        vendas = [t for t in ficha["transacoes"] if t["valor_declarado"]]
        if vendas:
            ult, pri = vendas[0], vendas[-1]
            ind = {"Transações registradas": len(ficha["transacoes"]),
                   "Última transação": f"{_data_br(ult['data'])} — {_moeda(ult['valor_declarado'])}",
                   "Valor venal de referência na última transação": _moeda(ult["valor_venal_referencia"])}
            if ult["valor_m2"]:
                ind["Valor por m² construído (última)"] = _moeda(ult["valor_m2"])
            if len(vendas) > 1 and pri["valor_declarado"]:
                var = (ult["valor_declarado"] / pri["valor_declarado"] - 1) * 100
                ind[f"Variação desde {_data_br(pri['data'])[-4:]}"] = f"{var:+.0f}%".replace(".", ",")
            ficha["indicadores"] = ind
            if ult["valor_venal_referencia"] and ult["valor_declarado"] < 0.8 * ult["valor_venal_referencia"]:
                ficha["alertas"].append("Valor declarado na última transação ficou bem abaixo do valor venal de referência "
                                        "(possível subdeclaração ou venda abaixo do mercado).")
            datas = sorted(t["data"] for t in vendas if t["data"])
            for a, b in zip(datas, datas[1:]):
                try:
                    if (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days < 365:
                        ficha["alertas"].append(f"Duas transações em menos de um ano ({_data_br(a)} e {_data_br(b)}).")
                        break
                except ValueError:
                    pass
        sit = (rec.get("situacao_sql") or "").lower()
        if sit and "ativ" not in sit and "normal" not in sit:
            ficha["alertas"].append(f"Situação do SQL na Prefeitura: {rec.get('situacao_sql')}.")
        sqln = itbi_sp.sql_formatado(rec.get("sql_n"))
        ficha["links"] = [(f"{t} — use o SQL {sqln}", u) for t, u in LINKS_SP]
        end = ficha["imovel"].get("Endereço")
        if end:
            ficha["links"].append(("Ver o endereço no mapa", "https://www.google.com/maps/search/?api=1&query="
                                   + urllib.parse.quote(f"{end}, {rec.get('bairro') or ''}, São Paulo - SP")))
    else:
        ficha["links"] = list(LINKS_SP)
    ficha["links"].append(LINK_MATRICULA)
    return ficha


def nome_arquivo(consulta: dict) -> str:
    base = "_".join(re.sub(r"[^0-9A-Za-z]", "", str(v)) for v in consulta.values() if v)
    return f"imovel_{base}_{datetime.now():%Y%m%d_%H%M}.html"


def salvar_html(ficha: dict) -> Path:
    PASTA_REL.mkdir(parents=True, exist_ok=True)
    arq = PASTA_REL / nome_arquivo(ficha["consulta"])
    arq.write_text(gerar_html(ficha), "utf-8")
    return arq


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def titulo(consulta: dict) -> str:
    if consulta.get("matricula"):
        return f"Matrícula nº {mat_fmt(consulta['matricula'])} — {consulta['cartorio']}º Oficial de Registro de Imóveis de São Paulo/SP"
    return f"SQL {consulta.get('sql')} — São Paulo/SP"


def gerar_html(ficha: dict) -> str:
    c = ficha["consulta"]
    im = "".join(f"<div><span>{_e(k)}</span><b>{_e(v)}</b></div>" for k, v in ficha["imovel"].items() if v)
    ind = "".join(f"<div class=kpi><b>{_e(v)}</b><span>{_e(k)}</span></div>" for k, v in ficha["indicadores"].items())
    trs = "".join(
        f"<tr><td class=d>{_e(_data_br(t['data']))}</td><td>{_e(t['natureza'])}</td><td class=n>{_moeda(t['valor_declarado'])}</td>"
        f"<td class=n>{_moeda(t['valor_venal_referencia'])}</td><td class=n>{_moeda(t['valor_m2'])}</td>"
        f"<td>{_e(t['financiamento'] or '—')}{(' · ' + _moeda(t['valor_financiado'])) if t['valor_financiado'] else ''}</td>"
        f"<td>{_e(t['proporcao'] or '')}</td></tr>" for t in ficha["transacoes"])
    alertas = "".join(f"<li>{_e(a)}</li>" for a in ficha["alertas"])
    links = "".join(f"<li><a href='{_e(u)}' target=_blank>{_e(t)}</a></li>" for t, u in ficha["links"])
    limites = "".join(f"<li>{_e(l)}</li>" for l in ficha["limites"])
    corpo = (f"""
<h2>Imóvel</h2><section class=card><div class=fatos>{im}</div></section>
{f'<section class="card alerta"><h2>Pontos de atenção</h2><ul>{alertas}</ul></section>' if alertas else ''}
<div class=kpis>{ind}</div>
<h2>Transações registradas (ITBI)</h2>
<div class=tw><table><tr><th>Data</th><th>Natureza</th><th>Valor declarado</th><th>Valor venal de referência</th>
<th>R$/m² construído</th><th>Financiamento</th><th>Proporção</th></tr>{trs}</table></div>"""
             if ficha["encontrado"] else
             """<section class="card alerta"><h2>Nenhuma transação encontrada</h2><p>Esta matrícula/SQL não aparece nas
planilhas de ITBI da Prefeitura. Pode ser um imóvel sem venda registrada desde 2006, recebido por herança ou doação,
de outro município, ou a guia de ITBI pode não ter informado a matrícula. Confira os números ou consulte pelo SQL do carnê.</p></section>""")
    return f"""<!doctype html><html lang=pt-BR><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Imóvel — {_e(titulo(c))}</title>
<style>
:root{{--bg:#f5f4ef;--fg:#1c1c1a;--mut:#6d6b64;--card:#fff;--line:#e2dfd6;--a:#2d6a4b;--p:#a63a28}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141412;--fg:#ecebe5;--mut:#a3a199;--card:#1e1e1b;--line:#35342f;--a:#72c296;--p:#e4806d}}}}
*{{box-sizing:border-box}} body{{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0 auto;padding:24px 16px;max-width:1100px}}
h1{{font-size:22px;margin:0}} h2{{font-size:17px;margin:24px 0 8px}} .mut{{color:var(--mut)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:10px 0}} .card.alerta{{border-left:5px solid var(--p)}}
.fatos{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px 20px}}
.fatos span{{display:block;font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.03em}} .fatos b{{font-weight:500}}
.kpis{{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}} .kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px}}
.kpi b{{display:block;font-size:18px}} .kpi span{{color:var(--mut);font-size:13px}}
table{{border-collapse:collapse;width:100%;font-size:14px;background:var(--card)}} td,th{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left}}
th{{color:var(--mut);font-weight:500}} .n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}} .d{{white-space:nowrap}} .tw{{overflow-x:auto}}
a{{color:var(--a)}}
</style>
<h1>{_e(titulo(c))}</h1>
<div class=mut>Fonte: planilhas abertas de ITBI da Prefeitura de São Paulo · gerado em {datetime.now():%d/%m/%Y %H:%M}</div>
{corpo}
<h2>Consultas oficiais para completar</h2><section class=card><ul>{links}</ul>
<p class=mut style="font-size:13px">Os serviços da Prefeitura pedem o SQL e podem exigir confirmação (captcha). A matrícula atualizada é emitida pelo cartório.</p></section>
<h2>Limites desta consulta</h2><ul class=mut>{limites}</ul>
</html>"""
