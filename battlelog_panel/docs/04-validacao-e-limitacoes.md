# Fase 6 — Testes, validação e limitações

`python -m pytest -q` → **52 testes, todos passando** (2026-11-03). Os testes usam *fixtures* sintéticas (`tests/fixtures/`, marcadas `_fixture_note: SYNTHETIC`) e um transporte offline. Eles provam a lógica do painel, **não** o formato real do Battlelog.

| # | Item da Fase 6 | Como foi verificado | Resultado |
|---|---|---|---|
| 1 | Conexão com fontes autorizadas | `blpanel probe` contra o Battlelog real | **Não concluído**: a rede do ambiente bloqueia o domínio (`network_blocked`, registrado). O tratamento do bloqueio foi testado: nenhum snapshot ou export falso é gerado e a sincronização fica "pendente" (`test_blocked_network_is_an_error_not_fake_data`). |
| 2 | Estatísticas correspondem à origem | `test_stats_match_source_values_exactly`: cada valor normalizado é igual ao campo da resposta e traz o caminho exato da origem | OK sobre fixtures. **Com dados reais: pendente** (rodar `probe` ou importar um HAR). |
| 3 | Integridade do parser | Ausente → `null`/`missing` (nunca 0); strings ignoradas; campos extras preservados; derivados só sem valor-fonte; dogtag sem campo de posse → `UNKNOWN` | OK |
| 4 | Simulações de estatísticas e dogtags | `test_lab.py`: set/add/multiply, clamp de negativos, K/D por ajuste de kills ou sobrescrita (com aviso de inconsistência), divisão por zero → `missing`, desbloqueio simulado, restaurar, remover, exportar | OK |
| 5 | Simulação não contamina o perfil real | `test_lab_never_touches_real_data`: snapshots, histórico de sync e `profile.json` idênticos byte a byte após operações do Lab | OK |
| 6 | Sincronização com o site | Export atômico, `data_classification: REAL`, histórico, diff entre syncs, import HAR, endpoint `/site/profile.json`; widget validado no navegador (Playwright) | OK |
| 7 | Registros de auditoria | Toda operação gera entrada classificada; tentativa de gravação real → `UNSUPPORTED/refused`; dados sensíveis redigidos | OK |
| 8 | Limitações documentadas | Abaixo e em `01-investigacao.md` | OK |

Outros testes: só GET e sem credenciais (`test_sync_makes_only_get_requests_without_credentials`), cabeçalhos de credencial rejeitados, limite de taxa, login/papéis, cookie adulterado rejeitado, cabeçalho anti-CSRF obrigatório, `--insecure-dev` recusado em produção, separação dos diretórios de teste e produção, portões do `AuthorizedWriteAdapter`.

**Teste de interface (Playwright, Chromium):** desktop (escuro) e celular 390 px (claro) passaram por todas as abas, sem rolagem horizontal. A única mensagem no console foi o 409 esperado da tentativa de gravação real (UNSUPPORTED).

## Limitações conhecidas

1. **Nenhuma resposta real do Battlelog foi observada** durante o desenvolvimento (rede bloqueada). Endpoints e nomes de campos são conhecimento prévio não verificado. Valide antes de publicar no site.
2. **Endpoint de dogtags é candidato.** Se não existir ou não expuser posse, o catálogo mostra "sem dados", ou todas como `UNKNOWN`.
3. **Escala de `accuracy`** (0–1 ou 0–100) desconhecida.
4. **Somente BF4.**
5. **Developer / Exclusive** é heurística (palavras-chave) até você preencher `data/dogtag_annotations.json` com IDs observados.
6. **Nenhuma gravação real é possível** (ver `03-viabilidade-gravacao.md`).
7. **Identidade visual do site:** o repositório não contém o seu site. A UI usa tokens CSS num único bloco (`blpanel/static/app.css`, `:root`) e o widget aceita variáveis `--blp-*`. Basta trocar as cores e a fonte pelas do seu site.
8. Sincronização automática acompanha o último perfil sincronizado online. O agendador interno roda dentro do processo do servidor.
