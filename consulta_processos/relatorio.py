"""Resumo + saídas JSON, CSV e HTML. O HTML se recarrega sozinho enquanto a consulta está em andamento."""
from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .documento import cnj_formatado
from .linguagem import explicar_classe
from .modelo import Processo

ROTULO_POLO = {"ATIVO": "Autor (movido por)", "PASSIVO": "Réu (contra)", "TERCEIRO": "Terceiro"}
CONF = {"DOCUMENTO_NO_TEXTO": "confirmado por documento", "NOME_EXATO": "nome idêntico",
        "NOME_SEMELHANTE": "nome semelhante — conferir", "CITADO_EM_PUBLICACAO": "citado em outro processo"}


def nome_base(documento: str) -> str:
    return f"dossie_{re.sub(r'[^0-9A-Za-z]', '', documento)}_{datetime.now():%Y%m%d_%H%M}"


def resumo(processos: list[Processo]) -> dict:
    c = lambda f: dict(Counter(f(p) or "não identificado" for p in processos).most_common())
    return {
        "total_processos": len(processos),
        "movidos_pelo_consultado": sum(1 for p in processos if p.polo_do_consultado == "ATIVO"),
        "contra_o_consultado": sum(1 for p in processos if p.polo_do_consultado == "PASSIVO"),
        "com_alertas": sum(1 for p in processos if p.alertas),
        "em_andamento": sum(1 for p in processos if (p.status_andamento or "").startswith("Em andamento")),
        "confirmados_por_documento": sum(1 for p in processos if p.como_foi_encontrado == "DOCUMENTO_NO_TEXTO"),
        "alertas": dict(Counter(a for p in processos for a in p.alertas).most_common()),
        "por_envolvido": c(lambda p: p.envolvido),
        "por_natureza": c(lambda p: p.natureza),
        "por_status": c(lambda p: (p.status_andamento or "").split(" (")[0]),
        "por_polo": c(lambda p: ROTULO_POLO.get(p.polo_do_consultado, "Não identificado")),
        "por_ramo": c(lambda p: p.ramo),
        "por_tribunal": c(lambda p: p.tribunal),
        "por_fase": c(lambda p: p.fase),
        "por_identificacao": c(lambda p: CONF.get(p.como_foi_encontrado, p.como_foi_encontrado)),
        "por_ano_distribuicao": dict(sorted(Counter(p.ano_ajuizamento or "não informado" for p in processos).items())),
    }


def salvar(saida: Path, consulta: dict, processos: list[Processo], extras: dict, base: str | None = None) -> dict[str, Path]:
    saida.mkdir(parents=True, exist_ok=True)
    base = base or nome_base(consulta["documento"])
    res = resumo(processos)
    completo = {"consulta": consulta, "resumo": res, **extras,
                "processos": [p.to_dict() | {"numero_cnj_formatado": cnj_formatado(p.numero_cnj)} for p in processos]}
    arquivos = {"html": saida / f"{base}.html", "json": saida / f"{base}.json", "csv": saida / f"{base}.csv"}
    _gravar(arquivos["json"], json.dumps(completo, ensure_ascii=False, indent=2, default=str))

    tmp = arquivos["csv"].with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["envolvido", "numero_cnj", "polo", "natureza", "ramo", "tribunal", "instancias", "classe", "fase",
                    "status_andamento", "resultado", "marcos", "alertas", "assuntos", "orgao_julgador",
                    "distribuicao", "ultima_atividade", "segredo", "qtd_movimentos", "qtd_publicacoes",
                    "identificacao", "fontes"])
        for p in processos:
            w.writerow([p.envolvido, cnj_formatado(p.numero_cnj), ROTULO_POLO.get(p.polo_do_consultado, ""),
                        p.natureza, p.ramo, p.tribunal, " | ".join(p.instancias), p.classe, p.fase,
                        p.status_andamento, p.resultado, " | ".join(p.marcos), " | ".join(p.alertas),
                        " | ".join(p.assuntos), p.orgao_julgador, p.data_distribuicao, p.data_ultima_movimentacao,
                        p.segredo_justica, len(p.movimentacoes), len(p.publicacoes),
                        CONF.get(p.como_foi_encontrado, p.como_foi_encontrado), ",".join(p.fontes)])
    tmp.replace(arquivos["csv"])
    _gravar(arquivos["html"], _html(consulta | {"_base": base}, res, processos, extras))
    return arquivos


def _gravar(arq: Path, texto: str) -> None:
    tmp = arq.with_suffix(arq.suffix + ".tmp")
    tmp.write_text(texto, "utf-8")
    tmp.replace(arq)  # troca atômica: quem estiver com o relatório aberto nunca vê arquivo pela metade


# ---------------------------------------------------------------- HTML
def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _moeda(v) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if v else "—"


def _contagem(titulo: str, d: dict) -> str:
    if not d:
        return ""
    total = sum(d.values()) or 1
    linhas = "".join(f"<tr><td>{_e(k)}</td><td class=n>{v}</td><td class=bar><i style='width:{100 * v / total:.0f}%'></i></td></tr>"
                     for k, v in list(d.items())[:12])
    return f"<div class=card><h3>{_e(titulo)}</h3><table>{linhas}</table></div>"


def _tabela(cab: list[str], linhas: list[list]) -> str:
    th = "".join(f"<th>{_e(c)}</th>" for c in cab)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in l) + "</tr>" for l in linhas)
    return f"<div class=tw><table class=full><tr>{th}</tr>{tr}</table></div>"


def _data_br(d) -> str:
    return f"{d[8:10]}/{d[5:7]}/{d[:4]}" if d and len(d) >= 10 and d[4] == "-" else (d or "—")


def _status_cls(s: str | None) -> str:
    s = (s or "").lower()
    if s.startswith("arquivado"):
        return "st-arq"
    if s.startswith("suspenso"):
        return "st-susp"
    if s.startswith("sem movimenta"):
        return "st-parado"
    return "st-ativo"


def _processo(p: Processo) -> str:
    polo = p.polo_do_consultado
    cls = {"ATIVO": "ativo", "PASSIVO": "passivo"}.get(polo, "outro")
    amigavel, _ = explicar_classe(p.classe)
    titulo = amigavel or p.classe or p.natureza or "Processo"
    alertas = "".join(f"<span class=al>{_e(a)}</span>" for a in p.alertas)
    partes = _tabela(["Lado", "Nome", "Advogados"], [
        [_e(ROTULO_POLO.get(pt.polo, pt.polo or "")), _e(pt.nome),
         _e("; ".join(f"{a.nome} (OAB {', '.join(a.oab)})" if a.oab else (a.nome or "") for a in pt.advogados))]
        for pt in p.partes]) if p.partes else "<p class=mut>As fontes abertas não publicaram as partes deste processo.</p>"
    movs = "".join(f"<tr><td class=d>{_e(_data_br(m.data))}</td><td>{_e(m.descricao)}"
                   f"{'<br><small class=mut>' + _e(m.complemento) + '</small>' if m.complemento else ''}</td></tr>"
                   for m in p.movimentacoes)
    pubs = "".join(
        f"<details class=pub><summary>{_e(_data_br(pb.get('data')))} — {_e(pb.get('tipo') or 'Publicação')}"
        f"{' · ' + _e(pb.get('orgao')) if pb.get('orgao') else ''}</summary>"
        f"<div class=texto>{_e(pb.get('texto'))}</div>"
        + (f"<a href='{_e(pb['link'])}' target=_blank>abrir documento original</a> " if pb.get("link") else "")
        + (f"<a href='{_e(pb['certidao'])}' target=_blank>certidão da publicação</a>" if pb.get("certidao") else "")
        + "</details>" for pb in p.publicacoes)
    tecnico = [
        ("Número CNJ", cnj_formatado(p.numero_cnj)), ("Classe oficial", p.classe), ("Código da classe", p.classe_codigo),
        ("Assuntos (tabela CNJ)", " | ".join(p.assuntos)), ("Ramo / tribunal", " – ".join(x for x in (p.ramo, p.tribunal) if x)),
        ("Instâncias", " | ".join(p.instancias)), ("Órgão julgador", p.orgao_julgador),
        ("Sistema / formato", " / ".join(x for x in (p.sistema, p.formato) if x)), ("Segredo de justiça", p.segredo_justica),
        ("Como foi identificado", CONF.get(p.como_foi_encontrado, p.como_foi_encontrado)), ("Fontes", ", ".join(p.fontes)),
        ("Processos citados nas publicações", ", ".join(cnj_formatado(n) for n in p.processos_citados)),
    ] + [(k, v) for k, v in p.informacoes_extras.items()]
    tecnico_html = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in tecnico if v not in (None, "", []))
    assunto = p.assuntos[0].split(">")[-1].strip() if p.assuntos else None
    fatos = [
        ("Do outro lado", ", ".join(p.parte_contraria) if p.parte_contraria else None),
        ("Assunto", assunto), ("Área", p.natureza), ("Fase", p.fase),
        ("Começou em", _data_br(p.data_distribuicao) if p.data_distribuicao else None),
        ("Resultado", p.resultado), ("Já aconteceu", " · ".join(p.marcos) if p.marcos else None),
        ("Onde", " – ".join(x for x in (p.tribunal, p.orgao_julgador) if x)),
    ]
    fatos_html = "".join(f"<div><span>{_e(k)}</span><b>{_e(v)}</b></div>" for k, v in fatos if v)
    ult = p.ultimo_ato or {}
    bloco_ult = (f"<div class=ultimo><span>Último ato publicado · {_e(_data_br(ult.get('data')))}"
                 f"{' · ' + _e(ult.get('tipo')) if ult.get('tipo') else ''}</span>"
                 f"<blockquote>{_e(ult.get('trecho'))}</blockquote></div>") if ult.get("trecho") else ""
    return f"""
<details class='proc {cls}' id='p{_e(p.numero_cnj)}'>
<summary>
  <div class=topo><span class=tag>{_e(ROTULO_POLO.get(polo, 'Lado não identificado'))}</span>
    <span class='st {_status_cls(p.status_andamento)}'>{_e(p.status_andamento or 'situação desconhecida')}</span>{alertas}</div>
  <h3>{_e(titulo)} <small>{_e(p.tribunal or '')} · {_e(cnj_formatado(p.numero_cnj))}</small></h3>
  <p class=resumo>{_e(p.resumo_simples)}</p>
  <div class=rodape>{_e(p.envolvido)} · <span class='conf c{_e(p.como_foi_encontrado)}'>{_e(CONF.get(p.como_foi_encontrado, p.como_foi_encontrado))}</span>
    · <span class=abrir>ver detalhes</span></div>
</summary>
<div class=corpo>
  {f'<div class=explica><b>O que é {_e(titulo.lower())}?</b> {_e(p.explicacao_classe)}</div>' if p.explicacao_classe else ''}
  <div class=fatos>{fatos_html}</div>
  {bloco_ult}
  <details class=sec><summary>Partes e advogados ({len(p.partes)})</summary>{partes}</details>
  <details class=sec><summary>Linha do tempo — andamentos ({len(p.movimentacoes)})</summary><div class=scroll><table class=full>{movs}</table></div></details>
  <details class=sec><summary>Publicações oficiais com texto integral ({len(p.publicacoes)})</summary>{pubs or '<p class=mut>Nenhuma.</p>'}</details>
  <details class=sec><summary>Dados técnicos</summary><table class=full>{tecnico_html}</table></details>
</div>
</details>"""


def _html(consulta: dict, res: dict, processos: list[Processo], extras: dict) -> str:
    andamento = consulta.get("em_andamento")
    kpis = "".join(f"<div class='kpi {c}'><b>{_e(v)}</b><span>{_e(k)}</span></div>" for k, v, c in [
        ("processos", res["total_processos"], ""), ("movidos por", res["movidos_pelo_consultado"], "ativo"),
        ("contra", res["contra_o_consultado"], "passivo"), ("em andamento", res["em_andamento"], ""),
        ("com alertas", res["com_alertas"], "passivo" if res["com_alertas"] else ""),
        ("confirmados por documento", res["confirmados_por_documento"], "")])

    # painel de alertas
    com_alerta = [p for p in processos if p.alertas]
    painel = ""
    if com_alerta or extras.get("sancoes_administrativas") or (extras.get("divida_ativa_uniao") or {}).get("total"):
        itens = "".join(f"<li><b>{_e(', '.join(p.alertas))}</b> — {_e(cnj_formatado(p.numero_cnj))} · {_e(p.tribunal)} · "
                        f"{_e(p.envolvido)} · {_e(p.status_andamento)}</li>" for p in com_alerta[:30])
        sanc = extras.get("sancoes_administrativas") or []
        if sanc:
            itens += f"<li><b>{len(sanc)} sanção(ões) administrativa(s)</b> em CEIS/CNEP</li>"
        div = extras.get("divida_ativa_uniao") or {}
        if div.get("total"):
            itens += f"<li><b>Dívida ativa da União</b>: {div['total']} inscrições, {_moeda(div.get('valor_total'))}, {div.get('ajuizadas', 0)} ajuizadas</li>"
        consta = [c for c in extras.get("certidoes_tcu") or [] if c.get("situacao") and "NADA" not in str(c["situacao"]).upper()]
        for c in consta:
            itens += f"<li><b>{_e(c.get('emissor'))} – {_e(c.get('cadastro'))}</b>: {_e(c.get('situacao'))} ({_e(c.get('empresa'))})</li>"
        painel = f"<section class='card alerta'><h2>Pontos de atenção</h2><ul>{itens}</ul></section>"

    # cadastro
    cad = extras.get("dados_cadastrais") or {}
    linhas_cad = "".join(f"<p><b>{_e(k.replace('_', ' ').capitalize())}:</b> {_e(', '.join(v) if isinstance(v, list) else v)}</p>"
                         for k, v in cad.items() if v)
    if extras.get("vinculos_federais"):
        linhas_cad += f"<p><b>Vínculos em bases federais:</b> {_e(', '.join(extras['vinculos_federais']))}</p>"
    for nome, sub in (extras.get("socios_pj") or {}).items():
        linhas_cad += f"<p><b>Sócio PJ {_e(nome)}:</b> {_e(sub.get('situacao_cadastral'))}, sócios: {_e(', '.join(sub.get('socios') or []))}</p>"
    bloco_cad = f"<section class=card><h2>Cadastro</h2>{linhas_cad}</section>" if linhas_cad else ""

    # restrições
    blocos = []
    if extras.get("certidoes_tcu"):
        blocos.append("<h2>Certidões consolidadas (TCU · CNJ/CNIA · CGU)</h2>" + _tabela(
            ["Empresa", "Emissor", "Cadastro", "Situação", "Observação"],
            [[_e(c.get("empresa")), _e(c.get("emissor")), _e(c.get("cadastro")), f"<b>{_e(c.get('situacao'))}</b>",
              _e(c.get("observacao"))] for c in extras["certidoes_tcu"]]))
    if "sancoes_administrativas" in extras:
        s = extras["sancoes_administrativas"]
        blocos.append("<h2>Sanções administrativas (CEIS/CNEP)</h2>" + (_tabela(
            ["Cadastro", "Sanção", "Órgão", "Processo", "Vigência", "Fundamentação"],
            [[_e(x["cadastro"]), _e(x["tipo_sancao"]), _e(x["orgao_sancionador"]), _e(x["numero_processo"]),
              f"{_e(x['data_inicio'])} → {_e(x['data_fim'])}", _e(x["fundamentacao"])] for x in s])
            if s else "<p class=mut>Nada consta.</p>"))
    div = extras.get("divida_ativa_uniao")
    if div is not None:
        blocos.append(f"<h2>Dívida ativa da União (PGFN)</h2><p>{div.get('total', 0)} inscrições · "
                      f"{div.get('ajuizadas', 0)} ajuizadas · total {_moeda(div.get('valor_total'))}</p>"
                      + (f"<p class=mut>{_e(div['observacao'])}</p>" if div.get("observacao") else "")
                      + (_tabela(["Inscrição", "Receita", "Situação", "Data", "Ajuizada", "Valor"],
                                 [[_e(i.get("inscricao")), _e(i.get("receita")), _e(i.get("situacao")), _e(i.get("data")),
                                   _e(i.get("ajuizado")), _moeda(i.get("valor"))] for i in div.get("inscricoes", [])])
                         if div.get("inscricoes") else ""))

    # processos agrupados por envolvido
    grupos: dict[str, list[Processo]] = defaultdict(list)
    for p in processos:
        grupos[(p.envolvido or "Não identificado").split(";")[0]].append(p)
    ordem = sorted(grupos, key=lambda g: (not g.startswith(("Empresa", "Pessoa")), g))
    secoes = "".join(
        f"<h2 class=grupo>{_e(g)} <span class=mut>— {len(grupos[g])} processos</span></h2>"
        + "".join(_processo(p) for p in grupos[g]) for g in ordem)

    por_quem: dict[str, list[str]] = defaultdict(list)
    for a in extras.get("alvos") or []:
        por_quem[a["quem"]].append(a["nome"])
    alvos = "".join(f"<li><b>{_e(q)}</b>" + (f" — também pesquisado como {_e(', '.join(n[1:]))}" if len(n) > 1 else "") + "</li>"
                    for q, n in por_quem.items())
    erros = "".join(f"<li>{_e(k)}: {_e(v)}</li>" for k, v in (extras.get("erros_por_fonte") or {}).items())
    limites = "".join(f"<li>{_e(l)}</li>" for l in extras.get("limites_da_consulta") or [])
    banner = (f"<div class=andamento id=aviso><span id=aviso-txt>Consulta em andamento — etapa: <b>{_e(consulta.get('etapa'))}</b>. "
              f"A página é atualizada quando surgirem processos novos; o que você abriu continua aberto.</span>"
              f" <button id=atualizar class=hidden>Atualizar agora</button></div>") if andamento else ""

    return f"""<!doctype html><html lang=pt-BR><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Dossiê {_e(consulta['documento'])}</title>
<style>
:root{{--bg:#f5f4ef;--fg:#1c1c1a;--mut:#6d6b64;--card:#fff;--line:#e2dfd6;--a:#2d6a4b;--p:#a63a28;--o:#7a7468;--warn:#fff4e5}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141412;--fg:#ecebe5;--mut:#a3a199;--card:#1e1e1b;--line:#35342f;--a:#72c296;--p:#e4806d;--o:#a8a296;--warn:#2e2417}}}}
*{{box-sizing:border-box}} body{{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0 auto;padding:24px 16px;max-width:1240px}}
h1{{font-size:24px;margin:0}} h2{{font-size:18px;margin:28px 0 10px}} .mut{{color:var(--mut)}}
.andamento{{background:var(--warn);border:1px solid var(--line);padding:10px 14px;border-radius:8px;margin:12px 0}}
.kpis{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}} .kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px;min-width:130px}}
.kpi b{{display:block;font-size:26px;font-variant-numeric:tabular-nums}} .kpi span{{color:var(--mut);font-size:13px}}
.kpi.ativo b{{color:var(--a)}} .kpi.passivo b{{color:var(--p)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px}} .card h3{{margin:0 0 6px;font-size:14px}}
.card.alerta{{border-left:5px solid var(--p)}} .card.alerta h2{{margin-top:4px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} td,th{{padding:5px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
.n{{text-align:right;font-variant-numeric:tabular-nums}} .d{{white-space:nowrap}} .bar{{width:35%}} .bar i{{display:block;height:6px;background:var(--o);border-radius:3px}}
.tw{{overflow-x:auto}} .scroll{{max-height:380px;overflow:auto}}
.proc{{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--o);border-radius:8px;margin:8px 0;padding:10px 14px}}
.proc.ativo{{border-left-color:var(--a)}} .proc.passivo{{border-left-color:var(--p)}}
summary{{cursor:pointer}} .l2{{margin-top:2px}} .l3{{font-size:13px;color:var(--mut)}}
.tag{{font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid currentColor;margin-right:6px}}
.ativo .tag{{color:var(--a)}} .passivo .tag{{color:var(--p)}} .outro .tag{{color:var(--o)}}
.al{{font-size:12px;background:var(--p);color:#fff;border-radius:4px;padding:1px 6px;margin-left:6px}}
.st{{font-weight:600}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:0 24px;margin-top:10px}} .grid p{{margin:3px 0}}
.texto{{white-space:pre-wrap;font-size:13px;padding:8px 10px;border-left:3px solid var(--line);margin:6px 0}}
.cNOME_SEMELHANTE{{color:var(--p)}} .cDOCUMENTO_NO_TEXTO{{color:var(--a)}}
input{{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);margin:8px 0}}
.execut{{font-size:17px;line-height:1.55;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:16px 0}}
.proc{{padding:14px 18px}} .proc>summary{{list-style:none}} .proc>summary::-webkit-details-marker{{display:none}}
.proc h3{{margin:6px 0 4px;font-size:17px}} .proc h3 small{{font-weight:400;color:var(--mut);font-size:13px}}
.topo{{display:flex;flex-wrap:wrap;gap:6px;align-items:center}}
.resumo{{margin:4px 0 6px;font-size:15px;line-height:1.55}}
.rodape{{font-size:13px;color:var(--mut)}} .abrir{{color:var(--a);text-decoration:underline}} .proc[open] .abrir{{display:none}}
.st{{font-size:12px;padding:1px 8px;border-radius:99px;font-weight:600}}
.st-ativo{{background:#dceee3;color:#1f5a3a}} .st-susp{{background:#fbeccc;color:#7a5200}} .st-arq{{background:#e7e5df;color:#55534c}} .st-parado{{background:#f3dfd9;color:#8a3324}}
@media (prefers-color-scheme:dark){{.st-ativo{{background:#21412f;color:#a5e0bd}} .st-susp{{background:#4a3b17;color:#f0d08a}} .st-arq{{background:#33322d;color:#c8c5bb}} .st-parado{{background:#4a2a22;color:#f0b1a2}}}}
.corpo{{margin-top:12px;border-top:1px solid var(--line);padding-top:12px}}
.explica{{background:var(--bg);border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:14px}}
.fatos{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px 20px;margin-bottom:12px}}
.fatos span{{display:block;font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.03em}} .fatos b{{font-weight:500}}
.ultimo span{{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.03em}}
blockquote{{margin:4px 0 12px;padding:8px 14px;border-left:3px solid var(--a);background:var(--bg);font-size:14px;line-height:1.55}}
details.sec{{border-top:1px solid var(--line);padding:8px 0}} details.sec>summary{{font-weight:600;font-size:14px}}
details.pub{{margin:6px 0}} th{{color:var(--mut);font-weight:500;width:28%}}
a{{color:var(--a)}} .grupo{{border-bottom:2px solid var(--line);padding-bottom:4px}}
button{{font:inherit;font-weight:600;border:0;border-radius:8px;padding:6px 14px;background:var(--a);color:#fff;cursor:pointer;margin-left:6px}} .hidden{{display:none}}
</style>
<body data-andamento="{1 if andamento else 0}" data-total="{len(processos)}" data-etapa="{_e(consulta.get('etapa'))}" data-base="{_e(consulta.get('_base'))}">
<h1>Dossiê judicial — {_e(consulta.get('nome') or '')}</h1>
<div class=mut>{_e(consulta['tipo'])} {_e(consulta['documento'])} · gerado em {_e(consulta['gerado_em'])} · fontes: {_e(', '.join(consulta['fontes_consultadas']))}</div>
{banner}
{f'<p class=execut>{_e(extras.get("resumo_executivo"))}</p>' if extras.get("resumo_executivo") else ''}
<div class=kpis>{kpis}</div>
{painel}
<h2>Processos ({len(processos)})</h2>
<p class=mut>Cada cartão começa com um resumo em português simples. Clique para ver o que significa o tipo de ação,
o último ato publicado pelo juiz, as partes, a linha do tempo e os textos completos. Barra verde: o investigado é autor;
vermelha: é réu.</p>
<input placeholder="Filtrar: número, tribunal, parte, natureza, status, texto…" oninput="const q=this.value.toLowerCase();for(const d of document.querySelectorAll('.proc'))d.style.display=d.textContent.toLowerCase().includes(q)?'':'none'">
{secoes or '<p class=mut>Nenhum processo encontrado até agora.</p>'}
<div class=cards>{bloco_cad}{f'<section class=card><h2>Investigados</h2><ul>{alvos}</ul></section>' if alvos else ''}</div>
<h2>Panorama</h2>
<div class=cards>{_contagem('Natureza', res['por_natureza'])}{_contagem('Status do andamento', res['por_status'])}
{_contagem('Posição do consultado', res['por_polo'])}{_contagem('Por envolvido', res['por_envolvido'])}
{_contagem('Ramo da Justiça', res['por_ramo'])}{_contagem('Tribunal', res['por_tribunal'])}
{_contagem('Fase', res['por_fase'])}{_contagem('Ano de ajuizamento', res['por_ano_distribuicao'])}</div>
{''.join(blocos)}
{f'<h2>Erros nas fontes</h2><ul>{erros}</ul>' if erros else ''}
<h2>Limites desta consulta</h2><ul class=mut>{limites}</ul>
<p class=mut><small>Fontes oficiais: DJEN e DataJud (CNJ), Receita Federal (dados abertos), CGU, TCU, PGFN.
Natureza, status e alertas são inferidos automaticamente das classes, movimentos e publicações. Não substitui certidão oficial.</small></p>
<script>{SCRIPT}</script>
</body></html>"""


# Atualização do relatório enquanto a consulta roda:
# - em http (tela de entrada) só recarrega quando o arquivo mudou de verdade (novos processos ou nova etapa);
# - em file:// (aberto direto do disco) recarrega periodicamente, mas espera enquanto um processo estiver aberto;
# - antes de recarregar, guarda os detalhes abertos e a rolagem, e restaura depois;
# - se em 3 minutos não aparecer nenhum processo novo, para e mostra o botão "Atualizar agora".
SCRIPT = r"""
(function(){
  const B = document.body.dataset, EM = B.andamento === "1", TOTAL = +B.total, ETAPA = B.etapa;
  const CHAVE = "dossie:" + B.base, INTERVALO = 15000;
  let st = {}; try { st = JSON.parse(sessionStorage.getItem(CHAVE) || "{}"); } catch (e) {}
  function mapa(){ const m = new Map();
    document.querySelectorAll(".proc").forEach(p => { m.set(p.id, p);
      p.querySelectorAll("details").forEach((d, i) => m.set(p.id + ":" + i, d)); });
    return m; }
  function salvar(){ const m = mapa();
    st.abertos = [...m].filter(([, d]) => d.open).map(([k]) => k); st.scroll = window.scrollY;
    try { sessionStorage.setItem(CHAVE, JSON.stringify(st)); } catch (e) {} }
  // restaura o que estava aberto e a posição da página
  if (st.abertos) { const m = mapa(); st.abertos.forEach(k => { const d = m.get(k); if (d) d.open = true; }); }
  if (st.scroll) window.scrollTo(0, st.scroll);
  salvar();
  document.addEventListener("toggle", salvar, true);
  window.addEventListener("scroll", () => { clearTimeout(window.__t); window.__t = setTimeout(salvar, 300); });
  if (!EM) return;
  const aviso = document.getElementById("aviso-txt"), botao = document.getElementById("atualizar");
  botao.classList.remove("hidden");
  botao.onclick = () => { salvar(); location.reload(); };
  function recarregar(){ salvar(); location.reload(); }
  function ciclo(){
    if (location.protocol.startsWith("http")) {
      // aberto pela tela do programa: só recarrega quando o relatório realmente mudou
      fetch(location.href, {cache: "no-store"}).then(r => r.text()).then(t => {
        const total = +((t.match(/data-total="(\d+)"/) || [])[1] || 0);
        const etapa = (t.match(/data-etapa="([^"]*)"/) || [])[1];
        const ainda = /data-andamento="1"/.test(t);
        if (total !== TOTAL || etapa !== ETAPA || !ainda) recarregar(); else setTimeout(ciclo, INTERVALO);
      }).catch(() => setTimeout(ciclo, INTERVALO));
    } else if ([...document.querySelectorAll(".proc")].some(p => p.open)) {
      // aberto direto do disco: não recarrega enquanto você lê um processo
      aviso.innerHTML = "Consulta em andamento. Atualização em espera enquanto você lê um processo — use o botão para atualizar.";
      setTimeout(ciclo, INTERVALO);
    } else recarregar();
  }
  setTimeout(ciclo, INTERVALO);
})();
"""
