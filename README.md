# consulta_processos — dossiê judicial só com o CPF ou CNPJ (fontes oficiais gratuitas)

## Jeito fácil: pela tela

| Sistema | O que fazer |
|---|---|
| **Linux** | Uma vez só: `./instalar-atalho-linux.sh`. Depois abra **Consulta de Processos** no menu de aplicativos ou na área de trabalho. Sem instalar o atalho, dê dois cliques em `Consultar-Processos.sh` ou rode esse arquivo no terminal |
| **Windows (sem Python)** | Pacote portátil: dois cliques em `Consulta de Processos.exe` (veja abaixo como gerar) |
| **Windows (com Python)** | Dois cliques em `Consultar-Processos.bat` |
| **Mac** | Dois cliques em `Consultar-Processos.command`. Na primeira vez: botão direito → Abrir |

O programa não depende de pacotes externos: só da biblioteca padrão do Python. A tela abre no navegador:

1. Digite o CPF ou CNPJ. A máscara e a validação são automáticas.
2. Clique em **Consultar**.
3. Acompanhe a etapa e quantos processos já foram encontrados. O botão **Abrir relatório** já funciona durante a consulta.
   O relatório só se atualiza quando surgem processos novos (o que você abriu continua aberto); o botão
   **Atualizar agora** atualiza na hora.
4. As consultas anteriores ficam listadas na própria tela.
5. **Encerrar programa** fecha o servidor local.

A tela roda só no seu computador (127.0.0.1); nada fica exposto na rede. Os relatórios ficam na pasta `relatorios` do programa.

## Pacote portátil para Windows (para levar a outros computadores)

Rode uma vez, em qualquer computador com Python e internet (o seu Linux serve):

```bash
python3 montar_windows.py
```

Ele baixa o Python oficial portátil da python.org e gera `dist/ConsultaProcessos-Windows.zip`, com:
- `Consulta de Processos.exe`
- o Python dentro da pasta `python`
- o programa

Nada é compilado: o `.exe` é o mesmo lançador que o `pip` usa no Windows e só abre o Python da própria pasta.

No outro computador, basta extrair o zip em qualquer pasta e dar dois cliques no `.exe`; não é preciso instalar nada. Se o Windows avisar "O Windows protegeu o computador", clique em **Mais informações → Executar assim mesmo**. O lançador não é assinado digitalmente, por isso o aviso.

`Diagnostico.bat` abre a tela com uma janela de mensagens, caso algo não funcione.

Sem internet no momento de montar, use `--python-zip arquivo.zip` com o `python-3.12.x-embed-amd64.zip` já baixado.

## Pela linha de comando

```bash
python -m consulta_processos 11.222.333/0001-81
```

Basta o documento. O resto o programa faz sozinho. O relatório HTML já existe desde o começo da execução. Ele se atualiza quando surgem processos novos. Aberto direto do disco, ele espera enquanto você estiver lendo um processo.

## O que ele faz, em ordem

| Etapa | Fonte oficial | Resultado |
|---|---|---|
| 1. Identidade | **CNPJ** → dados abertos da Receita · **CPF** → Portal da Transparência (CGU) | Razão social, nome fantasia, CNAE, capital, **sócios PF e PJ** · nome da pessoa e vínculos federais |
| 2. Descoberta | **DJEN** (CNJ) | Processos da empresa, de cada sócio e de cada sócio-empresa, encontrados pelas intimações publicadas |
| 3. Expansão | DJEN | Processos **citados** nas publicações (execuções, apensos, recursos) são verificados e entram no relatório se o investigado for parte |
| 4. Detalhe | **DataJud** (CNJ) + DJEN | Classe, assuntos, órgão, ajuizamento, instâncias (1º grau, 2º grau, turma recursal), todos os andamentos e o texto integral das intimações |
| 5. Análise | — | Natureza, ramo da Justiça, fase, **status do andamento**, **resultado**, marcos (sentença, recurso, trânsito, penhora) e **alertas** |
| 6. Restrições | CGU · TCU · PGFN | CEIS/CNEP, inidôneos do TCU, improbidade (CNIA), dívida ativa da União |

### Confiança da identificação
Cada processo indica como foi ligado ao investigado:
- **confirmado por documento**: o CPF/CNPJ aparece no texto da publicação. Para sócios, cujo CPF vem mascarado da Receita, valem os dígitos visíveis.
- **nome idêntico**.
- **nome semelhante — conferir**.

Use `--somente-confirmados` para manter só os do primeiro tipo.

### Natureza e status (inferidos)
- **Natureza**: Criminal, Falência/Recuperação, Improbidade, Execução fiscal, Trabalhista, Previdenciário, Família, Consumidor, Cobrança de dívida, Cível etc. Vem da classe e dos assuntos (tabelas do CNJ) e, na falta deles, do texto das intimações.
- **Status**: Em andamento, Suspenso, Arquivado/baixado ou "Sem movimentação há N meses", com o andamento que embasou a conclusão.
- **Resultado**: procedente, improcedente, acordo, extinção, condenação/absolvição, quando houver.

As regras ficam em `analise.py` e são fáceis de ajustar.

## Imóveis (bloco abaixo da busca por CPF/CNPJ)

### Por matrícula e cartório — só São Paulo capital
Informe, por exemplo, "Matrícula 32.404" e "3º Oficial de Registro de Imóveis de São Paulo". O programa monta a ficha do imóvel com:
- SQL, endereço, bairro e CEP;
- uso, padrão da construção e **ano de construção**;
- área construída, área do terreno, fração ideal e testada;
- **todas as vendas registradas desde 2006**: data, valor declarado, valor venal de referência, R$/m² e financiamento;
- valorização e alertas, por exemplo venda abaixo do valor venal ou duas vendas em menos de um ano;
- links oficiais já com o SQL: débitos de IPTU, certidão de débitos imobiliários e valor venal atual.

A ficha vem das planilhas abertas de ITBI da Prefeitura de São Paulo (2006 em diante). Na primeira busca, o programa baixa essas planilhas, monta um índice local na pasta `dados` e apaga os arquivos. Isso é feito uma vez só e leva de 15 a 40 minutos, conforme a internet. Depois as buscas são instantâneas, e **Atualizar** baixa só o que mudou, com a planilha do ano corrente saindo todo mês.

Limites:
- Só a capital publica esses dados. ABC e outras cidades não.
- Só aparecem imóveis com alguma transação com ITBI desde 2006. Herança e doação pagam ITCMD e não entram.
- Nomes de donos, penhoras, hipotecas, alienação fiduciária e indisponibilidade só constam na matrícula, emitida pelo cartório (serviço pago).

### Pelo carnê do IPTU — São Paulo e ABC
- **São Paulo:** com o SQL do carnê, o programa monta a mesma ficha.
- **Santo André, São Bernardo do Campo, São Caetano do Sul, Diadema, Mauá, Ribeirão Pires e Rio Grande da Serra:** essas prefeituras não publicam dados abertos de imóveis. O programa copia a inscrição e abre o portal oficial da prefeitura para a consulta do IPTU.

## Fontes e chaves (aba na tela)

Todas as fontes aparecem na aba **Fontes e chaves**, com o que cada uma traz. Em cada fonte você pode:
- ligar ou desligar o uso;
- colar a chave de acesso (mostrada sempre mascarada);
- testar a conexão.

As chaves ficam só neste computador, no arquivo `config.json` da pasta do programa, com permissão restrita ao seu usuário. Elas não ficam no código. Se você tinha um `.env` de versões anteriores, as chaves dele são importadas sozinhas na primeira execução, e depois o `.env` pode ser apagado.

| Grupo | Fontes |
|---|---|
| Oficiais gratuitas | Receita (CNPJ), Portal da Transparência (chave gratuita), DJEN, DataJud, TCU, PGFN (pasta com os arquivos) |
| Pagas (opcionais, desligadas até receberem chave) | Escavador, Judit |
| Personalizadas | qualquer API adicionada pelo assistente |

As fontes pagas e personalizadas buscam direto pelo CPF/CNPJ consultado. Os resultados são somados aos das fontes oficiais, sem duplicar processos, e cada processo mostra de quais fontes veio.

### Assistente para fontes novas
Em **Adicionar outra fonte (API)**, informe o nome, o endereço da API (ou da documentação), a chave e um CPF/CNPJ para teste. O programa então:
1. procura a especificação OpenAPI/Swagger nos endereços usuais;
2. escolhe as operações que aceitam CPF/CNPJ;
3. descobre a autenticação, pela especificação ou por tentativa (Bearer, `api-key`, `x-api-key`, token no endereço);
4. testa e reconhece sozinho os processos (qualquer objeto com número CNJ) e os campos: tribunal, classe, assuntos, datas, valor, partes e polos, andamentos. Funciona com nomes em português ou inglês, em qualquer nível do JSON, e segue a paginação.

Você vê uma amostra do que foi entendido e confirma. Se a API não publicar especificação, use um endereço de busca com `{documento}` no lugar do CPF/CNPJ.

Limite: APIs assíncronas (pede-se a busca agora e o resultado vem depois) ou com login em várias etapas precisam de conector próprio, como os de Escavador e Judit.

## Limites (comparado às APIs pagas)
- **CPF sem nome**: o nome só vem se a pessoa constar em alguma base federal (servidor, benefício, fornecedor, sanção…). Caso contrário, use `--nome`.
- **Cobertura**: o DJEN só traz processos que tiveram intimação depois que o tribunal aderiu (obrigatório desde 2025). Processos antigos e parados podem faltar. As pagas raspam os tribunais diretamente pelo documento.
- **Ausentes nas fontes abertas**: valor da causa, lista completa de partes com documento e processos em segredo de justiça.
- **CPF → empresas em que é sócio**: não há consulta reversa oficial gratuita.
- Natureza, status e alertas são inferência automática. Confira os casos relevantes no texto das publicações.
- A consulta envolve dados pessoais (LGPD). Tenha base legal. O relatório não substitui certidão oficial.

## Licença

MIT © 2026 Lucas Fracaro. Veja [LICENSE](LICENSE).
