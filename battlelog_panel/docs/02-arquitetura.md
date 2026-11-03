# Arquitetura

```
             (GET anônimo, sem cookies)                 (somente leitura)
Battlelog ─────────────────────────► BattlelogConnector ◄──── HAR / JSON importado
                                           │ RawResponse (url, status, horário, sha256, corpo)
                                           ▼
                                      ProfileParser ──► perfil normalizado (valor + origem + caminho-fonte)
                                           │
                 ┌─────────────────────────┼──────────────────────────┐
                 ▼                         ▼                          ▼
        StatisticsManager           DogtagManager                SyncManager ──► snapshots (SQLite)
        (catálogo, derivados,       (categorias, busca,               │       └─► site_export/profile.json ──► /site/profile.json ─► widget.js no seu site
         diff entre snapshots)       Developer/Exclusive)             │
                                                                      ▼
   ModificationEngine (Lab) ── lê snapshot, escreve só em lab_scenarios ── AuthorizedWriteAdapter (registro vazio → UNSUPPORTED)
                 │                                                      │
                 └──────────────────────── AuditLogger ◄────────────────┘  (todas as operações, com redação)
```

## Módulos (`battlelog_panel/blpanel/`)

| Módulo | Arquivo | Responsabilidade |
|---|---|---|
| BattlelogConnector | `connector.py` | Leitura. Só GET; recusa cabeçalhos de credencial; limite de taxa; classifica erros (`network_blocked`, `http`, `not_found`, `not_json`, `battlelog_error`); importação de HAR/JSON (ignora cabeçalhos e cookies do HAR). |
| ProfileParser | `parser.py` | Normaliza respostas. Cada estatística traz `value`, `origin` (`source`/`derived`/`missing`) e `source` (recurso + caminho JSON). Ausente = `null`, nunca 0. |
| StatisticsManager | `statistics.py` | Catálogo de estatísticas, campos candidatos, derivações (K/D, W/L, precisão, taxa de HS, kills/min, partidas), diff entre snapshots. |
| DogtagManager | `dogtags.py` | Catálogo, filtros, contagens e categorias. "Developer / Exclusive" por anotação curada ou heurística, sempre com a base exibida. Nunca altera o estado de posse. |
| ModificationEngine | `modification.py` | Profile Modification Lab: cenários sobre cópia profunda de um snapshot, operações set/add/multiply, K/D por "ajustar numerador" ou "sobrescrever" (com aviso de inconsistência), simulação de desbloqueio de dogtag, restaurar, exportar JSON/CSV com marca d'água. |
| AuthorizedWriteAdapter | `write_adapter.py` | Único caminho de gravação real. O registro de capacidades oficiais é **vazio**: tudo responde UNSUPPORTED com o motivo. Portões: flag, registro com documentação oficial, ambiente, frase de confirmação exata e confirmação do servidor. |
| SyncManager | `sync.py` | Sincronização manual/automática, importação, snapshots históricos, diff entre sincronizações, export atômico para o site, estado "pendente" com motivos. |
| AuditLogger | `audit.py` | Log append-only em SQLite: ator, ação, classificação (`READ`, `SYNC`, `SIMULATED`, `UNSUPPORTED`, `AUTHORIZED_WRITE`, `ADMIN`) e resultado, com detalhes redigidos. |
| Auth | `auth.py` | Tokens guardados só como SHA-256; cookie de sessão assinado (HMAC), HttpOnly, SameSite=Strict; papéis `viewer`/`admin`; cabeçalho anti-CSRF. |
| Server | `server.py` | API REST (Starlette), UI estática, endpoints públicos `/site/*`. |

## Classificação das operações

| Estado | Onde aparece | Significado |
|---|---|---|
| **REAL** (READ/SYNC) | Estatísticas, dogtags, export do site | Valor recebido de uma fonte real (live ou importada), com URL, horário e SHA-256. |
| **DERIVADO** | Cards de estatística | Calculado a partir de valores reais; mostra a fórmula. |
| **SIMULATED** | Somente no Lab | Valor alterado num cenário. Guardado em `lab_scenarios`; exportações levam marca d'água. |
| **AUTHORIZED WRITE** | Lab → "Aplicar na conta real" | Exigiria interface oficial registrada e confirmação do servidor. **Nunca ocorre hoje** (registro vazio). |
| **UNSUPPORTED** | Lab, aba Viabilidade, auditoria | Sem mecanismo oficial. O motivo exato é exibido e registrado. |

## Isolamento

* **Lab × dados reais:** o `ModificationEngine` lê snapshots e escreve apenas em `lab_scenarios`. O teste `test_lab_never_touches_real_data` compara byte a byte as tabelas de snapshots, o histórico de sincronização e o `profile.json` do site antes e depois de operações do Lab.
* **Teste × produção:** `BLPANEL_ENV=test|production` usa diretórios separados (`var/test/…`, `var/production/…`), com banco e export próprios. `--insecure-dev` (sem login) é recusado em produção.
* **Site × jogo:** o site só lê `/site/profile.json`, que contém apenas dados REAIS (`data_classification: "REAL"`; o widget recusa outro valor).

## Modelo de dados (SQLite)

`profiles` → `snapshots` (normalizado + bruto redigido + sha256) · `sync_runs` (gatilho, estado, erro, mudanças) · `lab_scenarios` · `audit_log`.

## API

| Método | Caminho | Papel |
|---|---|---|
| POST | `/api/login`, `/api/logout` | — |
| GET | `/api/status`, `/api/feasibility`, `/api/profiles`, `/api/profiles/{id}/current`, `/history`, `/dogtags?q=&status=&category=`, `/api/diff?a=&b=`, `/api/sync/runs`, `/api/lab/scenarios[/{id}]`, `/api/lab/scenarios/{id}/export?format=json\|csv`, `/api/audit?classification=` | viewer |
| POST | `/api/resolve`, `/api/sync`, `/api/import`, `/api/lab/scenarios/new`, `/api/lab/scenarios/{id}/modifications`, `/modifications/{mid}/delete`, `/restore`, `/delete`, `/apply-live` | admin + `X-Panel-Request: 1` |
| GET | `/site/profile.json`, `/site/widget.js` | público (CORS opcional, somente GET) |
