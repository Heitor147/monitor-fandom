# Fandom Watcher

Sistema pessoal de monitoramento de wikis da Fandom. Ele acompanha edições
nas páginas configuradas e a atividade no **Message Wall** (posts, respostas e
curtidas), e envia alertas por email e WhatsApp.

O projeto roda automaticamente no GitHub Actions, sem precisar manter um
computador ligado. O estado de cada wiki fica em [`state.json`](./state.json),
que o próprio workflow atualiza no repositório depois de uma notificação
entregue.

## Ra: a IA e o sistema de julgamento

**Ra é a própria camada de IA do sistema**: o nome dado ao agente que lê e
julga as edições monitoradas. Ele não é um usuário da Fandom nem uma pessoa
separada tomando decisões. Em produção, o Ra é executado pelo Gemini através
de [`ra.py`](./ra.py), integrado ao monitor em [`monitor.py`](./monitor.py).

Para cada edição relevante, o Ra:

1. lê o diff e o resumo escrito pelo editor;
2. recupera trechos relacionados das páginas configuradas como o cânon daquela
   wiki;
3. compara a alegação nova com esse material de referência;
4. devolve **um único veredito**:
   - `legítima`: edição normal, correção, formatação ou conteúdo coerente;
   - `suspeita`: remoção grande, categorias alteradas, conteúdo estranho ou
     possível contradição;
   - `vandalismo`: esvaziamento, spam, ofensa, texto sem sentido ou link
     promocional;
5. acrescenta um resumo curto e, quando aplicável, um relatório de cânon.

O relatório separa divergências confirmadas, pontos ambíguos e alegações
consistentes. Uma divergência só é aceita quando a página e a citação literal
de referência realmente conferem com o material enviado ao modelo. Essa
verificação reduz alucinações e impede que uma citação inventada seja tratada
como conflito. Uma divergência confirmada também impede que a edição seja
classificada como legítima.

O Ra **não aceita nem reverte automaticamente** uma edição: ele apenas produz
anotações para o dono revisar. A notificação sempre mantém os links para abrir
a página e para abrir o editor já preparado para desfazer a edição.

Edições cujo resumo indica desfazer ou reverter não são julgadas pelo Ra,
porque não representam uma alegação nova confiável. Elas recebem o resumo
simples da IA auxiliar em [`ai_summary.py`](./ai_summary.py). Se o Ra estiver
sem chave, atingir o limite da API ou falhar, o monitor continua funcionando:
o evento é enviado sem o julgamento, e o plano B tenta gerar apenas um resumo
quando possível.

## Como funciona

- O workflow [`monitor.yml`](./.github/workflows/monitor.yml) roda a cada
  10 minutos ou manualmente pela aba **Actions**.
- Para cada página, o sistema consulta as últimas revisões e compara o último
  `revid` salvo no estado.
- Para cada Message Wall habilitado, ele compara posts e contagens de
  respostas/curtidas com o estado anterior.
- A primeira execução cria o baseline do mural e não alerta sobre atividade
  antiga.
- Usuários ignorados não geram alertas de edição, post ou resposta.
- Cada wiki é processada isoladamente: uma falha de API em uma wiki não
  interrompe as demais.
- O Ra faz no máximo 8 julgamentos por execução, com intervalo para respeitar
  o plano gratuito do Gemini.
- O estado só é salvo quando pelo menos um canal de notificação entrega o
  resultado. Se nenhum canal estiver configurado ou todos falharem, o estado
  não avança e o evento será tentado novamente no próximo ciclo.

O email recebe o relatório completo do Ra. O WhatsApp recebe uma versão
compacta de até 600 caracteres por evento, preservando os links de aceitar e
reverter.

## Configuração

### 1. Criar o repositório

Crie um repositório novo no GitHub, que pode ser privado, e envie os arquivos
deste projeto.

### 2. Configurar `config.json`

O formato atual permite definir páginas e Message Wall por wiki:

```json
{
  "profile": {
    "username": "The_One_Who_Theorizes",
    "user_id": "45379286"
  },
  "ignored_users": ["The_One_Who_Theorizes"],
  "wikis": {
    "camp-halfblood-fanon": {
      "pages": ["Hera_(The_One_Who_Theorizes)"]
    },
    "hazbin-hotel-fanon": {
      "pages": []
    },
    "supernaturalfanon": {
      "pages": [],
      "wall": false
    }
  }
}
```

- `profile.username` e `profile.user_id` identificam o dono do Message Wall.
- `wikis` é um mapa em que a chave é o nome da wiki na URL da Fandom.
- `pages` contém títulos monitorados; espaços devem ser escritos como
  underscores.
- `wall` é `true` por padrão quando há `profile`; use `false` para desativar
  o mural naquela wiki.
- `ignored_users` usa comparação sem diferenciar maiúsculas/minúsculas, e
  espaços e underscores são equivalentes.
- Se uma página não existir em determinada wiki, ela é simplesmente ignorada.

O monitor ainda aceita configurações antigas:

```json
{
  "wiki": ["camp-halfblood-fanon", "supernaturalfanon"],
  "pages": ["Minha_Pagina"]
}
```

Nesse formato, as mesmas páginas são usadas em todas as wikis. O estado antigo
também é migrado para a estrutura separada por wiki quando possível.

### 3. Configurar WhatsApp com CallMeBot

1. Salve **+34 644 95 42 75** nos contatos.
2. Envie `I allow callmebot to call me` para esse número no WhatsApp.
3. Aguarde a resposta com sua API key.
4. Guarde seu número no formato internacional, por exemplo
   `5511999999999`.

O número e a mensagem de ativação podem mudar. Consulte a documentação atual
em <https://www.callmebot.com/blog/free-api-whatsapp-messages/>.

### 4. Configurar email com Resend

1. Crie uma conta em <https://resend.com>.
2. Em **API Keys → Create API Key**, crie uma chave (normalmente começa com
   `re_`).
3. No plano gratuito, `onboarding@resend.dev` é usado como remetente e não
   exige domínio próprio para o uso previsto.

### 5. Configurar os secrets do GitHub

No repositório, abra **Settings → Secrets and variables → Actions → New
repository secret** e crie:

| Secret | Uso |
|---|---|
| `RESEND_API_KEY` | Chave da API do Resend |
| `NOTIFY_EMAIL` | Endereço que receberá os emails |
| `CALLMEBOT_PHONE` | Número do WhatsApp em formato internacional |
| `CALLMEBOT_APIKEY` | API key fornecida pelo CallMeBot |
| `GEMINI_API_KEY` | Chave usada pelo Ra e pelo resumo auxiliar |

Opcionalmente, `GEMINI_MODEL` escolhe outro modelo Gemini. Se não for
definido, o código usa `gemini-3.5-flash-lite`.

### 6. Executar

O workflow já executa sozinho a cada 10 minutos. Para rodar imediatamente,
abra **Actions → Fandom Watcher → Run workflow**.

## Desenvolvimento local

O projeto requer Python 3.12 no workflow e usa as dependências de
[`requirements.txt`](./requirements.txt).

Para testar a lógica do Ra sem internet nem chamadas ao Gemini:

```powershell
python test_ra.py
```

Para analisar uma edição real sem enviar notificações:

```powershell
$env:GEMINI_API_KEY = "sua-chave"
python ra_demo.py camp-halfblood-fanon "Hera_(The_One_Who_Theorizes)"
```

O `ra_demo.py` procura uma edição recente feita por outra pessoa e imprime os
formatos de email e WhatsApp. Use `--any` apenas para testes em que também
pode ser escolhida uma edição do próprio dono.

## Coleta e avaliação de dados

Além do monitor em produção, o repositório contém ferramentas para estudar o
histórico das wikis:

- [`collect_data.py`](./collect_data.py) baixa revisões via API, identifica
  edições revertidas por tags do MediaWiki ou pela restauração do hash da
  página e grava JSONL em `data/`.
- [`collect_text.py`](./collect_text.py) baixa o texto da edição e do pai,
  calcula características do diff (caracteres adicionados/removidos,
  categorias, URLs, esvaziamento, repetições e texto) e cria
  `data/text_features.jsonl`.
- [`train_baseline.py`](./train_baseline.py) compara uma regra simples,
  gradient boosting e regressão logística com metadados.
- [`train_text.py`](./train_text.py) compara metadados, características
  numéricas e TF-IDF do texto adicionado/removido e do comentário.

Esses scripts formam a base de dados e avaliação experimental do projeto; o
juiz usado pelo monitor atualmente é o Ra/Gemini com recuperação de contexto,
não um classificador treinado por esses scripts.

Exemplos:

```powershell
python collect_data.py --wiki camp-halfblood-fanon --max 20000
python collect_text.py --limit 100
python train_baseline.py
python train_text.py
```

## Limitações e cuidados

- A API do Message Wall
  (`Fandom\MessageWall\MessageWall`) não é documentada oficialmente pela
  Fandom e pode mudar sem aviso.
- O julgamento do Ra depende da disponibilidade, limites e respostas do
  Gemini. O sistema não deve ser tratado como decisão automática definitiva.
- O cânon usado pelo Ra é formado pelas páginas listadas em `pages` na mesma
  wiki, além da versão anterior da página editada. Páginas não configuradas
  não entram na referência.
- O Ra trabalha com até oito edições por execução e não julga desfazer/reverter.
- O texto do diff enviado ao modelo é limitado para manter o custo e o
  tamanho das notificações sob controle.
- O CallMeBot pode limitar o tamanho das mensagens e apresentar instabilidade
  por ser um serviço gratuito de terceiros.
- Comentários de artigo são diferentes do Message Wall e não são monitorados
  por este projeto.
