# Fase 1 — Investigação técnica do Battlelog

> **Leia primeiro: níveis de evidência.** Este documento separa três coisas:
>
> | Marca | Significado |
> |---|---|
> | **OBSERVADO** | Verificado nesta sessão de desenvolvimento, com comando e resultado registrados. |
> | **CONHECIMENTO PRÉVIO (não verificado)** | Comportamento público do Battlelog conhecido antes deste projeto. **Não** foi confirmado nesta sessão. É uma hipótese de trabalho, implementada de forma configurável e validável. |
> | **DESCONHECIDO** | Não sabemos. O painel trata como ausente, nunca inventa um valor. |
>
> Nenhum endpoint, campo ou resposta neste documento foi inventado para preencher lacunas. Quando não há evidência, isso está escrito.

## 1. O que foi observado nesta sessão

| # | Verificação | Resultado | Marca |
|---|---|---|---|
| O1 | Inspeção da skill *Reverse Engineer Anything* (REA) | A skill trabalha com binários/pacotes, observação de navegador em tempo de execução (`list_browser_targets`, `capture_browser_scenario`) e capturas gravadas (`inspect_web_network_capture` para HAR/SAZ). Para um site como o Battlelog, as rotas aplicáveis são **observação no navegador** ou **análise de um HAR**. | OBSERVADO |
| O2 | Ferramentas REA (MCP) nesta sessão | Não registradas no ambiente de nuvem e nenhuma captura HAR fornecida. A análise REA não pôde ser executada. | OBSERVADO |
| O3 | `GET https://battlelog.battlefield.com/bf4/` | `CONNECT tunnel failed, response 403`: a política de rede do ambiente bloqueia o domínio. | OBSERVADO |
| O4 | `GET https://api.gametools.network/...` (API comunitária) | Mesmo bloqueio 403 da política de rede. | OBSERVADO |
| O5 | `python -m blpanel probe --persona-id 1 --platform pc` | `overview` e `detailed` → `network_blocked` com as URLs exatas registradas em `var/test/captures/probe-*/report.json`. | OBSERVADO |
| O6 | Repositório existente | Contém apenas `jugg_edit/` (pipeline de vídeo em Python/ffmpeg). Não há site nem código Battlelog. O painel foi criado como módulo novo e independente (`battlelog_panel/`). | OBSERVADO |

**Consequência:** nenhuma resposta real do Battlelog foi vista nesta sessão. Tudo sobre formatos e endpoints abaixo é **conhecimento prévio não verificado**. O projeto inclui as ferramentas para verificá-lo (seção 7).

## 2. Arquitetura pública do Battlelog (CONHECIMENTO PRÉVIO, não verificado)

* **Aplicação web renderizada no servidor com navegação AJAX.** O Battlelog (BF3 em 2011; BF4 com codinome interno *warsaw*; BF Hardline) serve páginas HTML. Quando a navegação interna envia os cabeçalhos AJAX (`X-AjaxNavigation: 1` / `X-Requested-With: XMLHttpRequest`), as mesmas URLs respondem JSON com o *contexto* que os templates do cliente renderizam.
* **Endpoints de dados `warsaw*populate`** (BF4): URLs GET que o próprio front-end chama para preencher abas de estatísticas. Respondem um envelope `{"type": "success"|"error", "message": "...", "data": {...}}`.
* **Leitura pública:** perfis e estatísticas de soldados são visíveis sem login no site. O painel usa apenas essas leituras anônimas.
* **Escrita:** ações como editar emblema, equipar dogtag ou alterar loadout exigem login EA. Elas usam sessão por cookie e um token anti-CSRF (`post-check-sum`) em requisições POST privadas do front-end. **Não são uma API pública ou documentada** e estão fora do escopo (ver relatório de viabilidade).

## 3. Identificadores e relações

```
Conta EA / usuário Battlelog ── userId (string numérica longa), username
   └── soldados (personas), um por plataforma
         ├── personaId   (numérico)
         ├── personaName (nome do soldado)
         ├── namespace   (ex.: cem_ea_id, ps3, xbox ...)
         └── platform    (inteiro)
```

| Plataforma | Inteiro (prévio, não verificado) |
|---|---|
| PC | 1 |
| Xbox 360 | 2 |
| PS3 | 4 |
| PS4 | 32 |
| Xbox One | 64 |

O código numérico do jogo em `soldiersBox[].game` (BF4 ≈ 2048) é **DESCONHECIDO/incerto**. O painel guarda o valor bruto (`game_raw`) sem interpretá-lo.

## 4. Endpoints de leitura usados pelo painel

Todos são `GET` anônimos com `User-Agent` identificável e intervalo mínimo de 2 s entre requisições. Nenhum envia cookie ou token.

| Recurso | Caminho | Finalidade | Evidência |
|---|---|---|---|
| identity | `/bf4/user/{username}/` | userId + lista de soldados (`soldiersBox[].persona.personaId/personaName`, `platform`) | Prévio, não verificado |
| overview | `/bf4/warsawoverviewpopulate/{personaId}/{platform}/` | `data.overviewStats.*` | Prévio, não verificado |
| detailed | `/bf4/warsawdetailedstatspopulate/{personaId}/{platform}/` | `data.generalStats.*` | Prévio, não verificado |
| dogtags | `/bf4/soldier/{name}/dogtags/{personaId}/{platform}/` (configurável: `BLPANEL_DOGTAGS_PATH`) | catálogo + posse, **se** exposto | **Candidato**, não verificado |

**Endpoints oficialmente documentados pela EA:** nenhum conhecido. A EA não publica portal de desenvolvedor, OAuth para terceiros ou documentação de API do Battlelog. Os caminhos acima são as URLs públicas que o próprio site carrega. Não são um contrato estável.

**APIs comunitárias** (ex.: gametools.network): documentadas pelos próprios autores, **não oficiais da EA**, somente leitura. Podem virar um conector alternativo; não foram integradas porque também estavam bloqueadas (O4) e não puderam ser verificadas.

## 5. Estruturas de dados (campos esperados, não verificados)

`overviewStats` / `generalStats`: o painel procura estes nomes e registra, para cada valor, o caminho exato de onde veio:

| Estatística | Campos tentados (em ordem) |
|---|---|
| Kills / Deaths | `kills`, `deaths` |
| K/D | `kdRatio`; senão calculado kills ÷ deaths (marcado **DERIVADO**) |
| Score / Skill / SPM | `score`, `skill`, `scorePerMinute` |
| W/L | `numWins`, `numLosses`, `wlRatio`; senão calculado |
| Headshots | `headshots` (+ taxa calculada headshots ÷ kills) |
| Precisão | `accuracy`; senão `shotsHit ÷ shotsFired`. **Escala desconhecida** (0–1 ou 0–100): exibida como reportada até verificação |
| Tempo de jogo | `timePlayed` (segundos, prévio) |
| Partidas | `numRounds`; senão vitórias + derrotas |
| Outras | `rank`, `killAssists`, `longestHeadshot`, `revives`, `heals`, `resupplies`, `repairs` |

Todo campo numérico **não mapeado** aparece em "Outras estatísticas disponíveis" com o nome original. Assim nada que a fonte retorne é escondido.

**Dogtags:** formato **DESCONHECIDO**. O parser procura listas sob chaves que contenham "dogtag"/"tag", com entradas que tenham ID e nome. O estado de posse é atribuído **somente** a partir de um campo explícito (`unlocked`, `isUnlocked`, `owned`, … ou data de desbloqueio). Sem esse campo, o estado é **UNKNOWN**, nunca "desbloqueada".

## 6. Autenticação, limitações e sincronização

* **Métodos de autenticação permitidos para um app de terceiros:** nenhum. Não existe login delegado (OAuth) do Battlelog para terceiros. Reutilizar cookies de sessão do usuário seria reutilização indevida de sessão e foi **excluído por design**: o conector rejeita cabeçalhos `Cookie`/`Authorization`.
* **O que pode ser sincronizado com o seu site:** estatísticas e dados públicos de perfil, em sentido único (Battlelog → painel → site), com origem e horário de cada valor.
* **Limitações:**
  1. Endpoints não oficiais podem mudar ou sair do ar sem aviso. O estado do serviço Battlelog em 2026 é **DESCONHECIDO** (sem acesso de rede nesta sessão).
  2. Apenas BF4 é suportado (o BF3 usa outros caminhos).
  3. Configurações de privacidade do perfil podem ocultar dados: **DESCONHECIDO**.
  4. Revise os termos de uso da EA antes de usar automação. O painel usa poucas requisições, espaçadas, e apenas leitura.

## 7. Como transformar "não verificado" em "verificado"

1. **Liberar a rede** do ambiente para `battlelog.battlefield.com`, ou rodar o painel numa máquina com acesso.
2. Rodar `python -m blpanel probe --username SEU_USUARIO --platform pc`. O comando salva cada resposta (redigida) e um `report.json` com o mapeamento campo a campo (`origin: source|derived|missing`).
3. **Alternativa sem rede no servidor:** abra seu perfil no navegador, DevTools → Network → *Save all as HAR*, e importe em *Conexão → Importar captura* (ou `python -m blpanel import arquivo.har`). Esse HAR também pode ser analisado com a ferramenta REA `inspect_web_network_capture` para documentar a sequência real de requisições.
4. Com evidência real, atualize o campo `evidence` em `blpanel/connector.py` (`ENDPOINTS`) para `VERIFIED` e ajuste nomes de campos em `blpanel/statistics.py`, se necessário.
