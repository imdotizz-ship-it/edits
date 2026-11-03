# Relatório de viabilidade das operações de gravação

**Conclusão:** nenhuma operação de gravação em uma conta Battlelog real pode ser feita por meios oficiais e autorizados. O `AuthorizedWriteAdapter` foi entregue com registro de capacidades **vazio**. Toda tentativa responde `UNSUPPORTED` e fica registrada na auditoria. As modificações existem apenas no Profile Modification Lab, como `SIMULATED`.

| Operação | Mecanismo oficial? | Estado no painel | Por quê |
|---|---|---|---|
| Ler estatísticas públicas | Não há API oficial; as páginas públicas são lidas anonimamente | **Leitura implementada** (endpoints a verificar) | Dados públicos, GET anônimo, sem contornar nada. |
| Ler dogtags | Idem; endpoint candidato | **Leitura implementada, endpoint não verificado** | Formato desconhecido; posse só com campo explícito. |
| Alterar kills, deaths, skill, score, K/D, W/L, precisão, tempo, partidas | **Não existe** | `UNSUPPORTED` · só Lab | Estatísticas são calculadas pelo backend do jogo a partir da telemetria das partidas. O Battlelog apenas as exibe. Nem o próprio dono da conta consegue editá-las. Qualquer "alteração" exigiria falsificar dados nos servidores da EA. |
| Conceder / desbloquear dogtag (inclusive Developer / Exclusive) | **Não existe** | `UNSUPPORTED` · só Lab | A posse é concedida pelo backend quando o critério é cumprido, ou pela EA/DICE (itens de desenvolvedor/evento). Não há interface para o jogador ou terceiros concederem itens protegidos. |
| Equipar dogtag já possuída / editar emblema | Só pela interface do Battlelog ou do jogo, pelo próprio dono logado | `UNSUPPORTED` no painel | O front-end usa POSTs privados autenticados por cookie de sessão e token CSRF. Não é API oficial nem documentada. Automatizá-los exigiria reutilizar a sessão/token do usuário, o que o projeto proíbe. O dono pode fazer isso manualmente no site ou no jogo. |
| Sincronizar com o seu site | Sim (dados próprios) | **Implementado** | Unidirecional; nada é enviado aos servidores do jogo. |

## Itens explicitamente fora do escopo

Injeção de pacotes, manipulação de memória do jogo, alteração de bancos de dados da EA, falsificação de requisições autenticadas, reutilização de tokens/sessões e uso de endpoints privados de escrita. Nada disso existe no código. O conector só faz GET e rejeita cabeçalhos de credencial (testado).

## Como uma gravação poderia ser habilitada no futuro

Somente se a EA/DICE publicar uma interface oficial de escrita para terceiros. Nesse caso:

1. Registrar uma `WriteCapability` em `OFFICIAL_CAPABILITIES` (`blpanel/write_adapter.py`), com `documentation_url` oficial, ambientes autorizados e um `executor` que use o mecanismo de autenticação oficial (nunca cookies de sessão do site).
2. Ativar `BLPANEL_WRITES_ENABLED=true`, primeiro no ambiente `test`.
3. A UI exigirá a frase exata `CONFIRM <op> <alvo>`. O resultado só vira `AUTHORIZED_WRITE` se o executor devolver `{"confirmed": true, ...}` vindo do servidor. Caso contrário, é `ERROR`, nunca sucesso.

Os portões estão cobertos por `tests/test_write_adapter.py`, que usa uma capacidade fictícia **apenas dentro do teste**.
