# Battlelog Profile Control Panel

Painel administrativo para **ler** um perfil Battlelog (BF4), organizar estatísticas e dogtags, testar alterações num **laboratório isolado** e publicar os dados reais no seu site.

* **Leitura real:** GET anônimo às páginas públicas do Battlelog, ou import de HAR/JSON capturado no seu navegador. Cada valor mostra origem, caminho JSON e horário.
* **Profile Modification Lab:** cenários `SIMULATED` sobre cópia de um snapshot, com comparação Before/After, restaurar e exportar.
* **Gravação real:** `UNSUPPORTED` para tudo. Não existe interface oficial; veja [docs/03-viabilidade-gravacao.md](docs/03-viabilidade-gravacao.md).

> ⚠️ Os endpoints do Battlelog **não foram verificados** durante o desenvolvimento: a rede do ambiente bloqueou o domínio. Rode o `probe` (abaixo) antes de confiar nos dados. Detalhes em [docs/01-investigacao.md](docs/01-investigacao.md).

## Instalação

Requer Python 3.11+.

```bash
cd battlelog_panel
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                       # 52 testes
```

## Configuração (variáveis de ambiente)

| Variável | Padrão | Uso |
|---|---|---|
| `BLPANEL_ENV` | `test` | `test` ou `production` (banco e export separados) |
| `BLPANEL_DATA_DIR` | `./var` | onde ficam `{env}/panel.sqlite3`, `site_export/`, `captures/` |
| `BLPANEL_ADMIN_TOKEN_SHA256` | — | digest do token admin (`python -m blpanel create-token`) |
| `BLPANEL_VIEWER_TOKEN_SHA256` | — | digest do token somente leitura (`create-token --role viewer`) |
| `BLPANEL_SESSION_SECRET` | aleatório por processo | segredo HMAC dos cookies (defina para manter sessões após reinício) |
| `BLPANEL_AUTO_SYNC_MINUTES` | `0` | sincronização automática (0 = desligada) |
| `BLPANEL_SITE_CORS` | — | origens do seu site, separadas por vírgula, para `/site/profile.json` |
| `BLPANEL_MIN_INTERVAL` | `2` | segundos mínimos entre requisições ao Battlelog |
| `BLPANEL_DOGTAGS_PATH` | `/{game}/soldier/{persona_name}/dogtags/{persona_id}/{platform}/` | caminho candidato da página de dogtags |
| `BLPANEL_WRITES_ENABLED` | `false` | sem efeito prático hoje: o registro oficial de gravação está vazio |

Guarde os valores num `.env` fora do git. Nenhum token em texto puro é salvo pelo painel.

## Uso

```bash
python -m blpanel create-token                     # mostra o token (uma vez) e o digest
export BLPANEL_ADMIN_TOKEN_SHA256=<digest>
python -m blpanel serve --port 8787                # http://127.0.0.1:8787
python -m blpanel serve --insecure-dev             # sem login; só ambiente test e localhost

# 1) validar os endpoints contra o Battlelog real (salva evidências em var/<env>/captures/)
python -m blpanel probe --username SEU_USUARIO --platform pc
# 2) sincronizar / importar pela linha de comando
python -m blpanel resolve SEU_USUARIO
python -m blpanel sync <personaId> --platform pc --name <soldado>
python -m blpanel import meu-perfil.har            # DevTools → Network → Save all as HAR
```

Num servidor de produção, rode atrás de HTTPS (proxy reverso) com `BLPANEL_ENV=production`. O cookie de sessão passa a ser `Secure`.

## Integração com o seu site

O seu site **lê**; o painel nunca recebe nada do site nem grava no jogo.

```html
<div data-blpanel-widget data-src="https://SEU-PAINEL/site/profile.json"
     style="--blp-accent:#sua-cor; --blp-font:'Sua Fonte'"></div>
<script src="https://SEU-PAINEL/site/widget.js" defer></script>
```

Ou consuma o JSON diretamente: `GET /site/profile.json` (schema `blpanel.site-export/1`, com `data_classification: "REAL"`, origem, horário, SHA-256 da resposta bruta e histórico). O mesmo arquivo é gravado em `var/<env>/site_export/profile.json`, para sites estáticos que copiem o arquivo.

### Build personalizado público (showcase)

No *Modification Lab*, o botão **Publicar no site (custom)** grava o cenário em `/site/showcase.json`. Qualquer visitante pode ver, usando o mesmo widget:

```html
<div data-blpanel-widget data-src="https://SEU-PAINEL/site/showcase.json"></div>
```

O widget sempre mostra o selo **"Build personalizado · não é estatística oficial do Battlelog"**, marca cada número alterado como `CUSTOM` e mostra o valor real ao lado. Não há opção para esconder o selo, e o CSS do site não alcança o widget. O `/site/profile.json` (dados reais) não é alterado. Para despublicar: `POST /api/showcase/unpublish`.

Para seguir a identidade visual do seu site, edite só o bloco de tokens no topo de `blpanel/static/app.css`.

## Documentação

1. [Investigação técnica](docs/01-investigacao.md): arquitetura do Battlelog, identificadores, endpoints e níveis de evidência
2. [Arquitetura](docs/02-arquitetura.md): módulos, isolamento e API
3. [Viabilidade de gravação](docs/03-viabilidade-gravacao.md)
4. [Validação e limitações](docs/04-validacao-e-limitacoes.md)
