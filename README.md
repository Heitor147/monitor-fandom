# Fandom Watcher

Monitora edições em páginas da Fandom e atividade no seu Message Wall
(novos posts, respostas e curtidas), e avisa por email e WhatsApp.

## Como funciona

Roda a cada 10 minutos via GitHub Actions (de graça, sem precisar de PC
ligado). Compara o estado atual da wiki com o último estado salvo
(`state.json`, que o próprio workflow commita de volta no repositório) e
manda notificação só do que for novo.

- **Edição de página**: manda link pra "aceitar" (só vai pra página) e
  link de "reverter" que já abre o editor com a edição desfeita, pronta
  pra você só confirmar e salvar.
- **Mural (Message Wall)**: post novo, resposta nova, ou curtida nova.

## Setup

### 1. Criar o repositório

Crie um repositório novo (pode ser privado) no GitHub e suba estes
arquivos.

### 2. Configurar `config.json`

Já vem preenchido com a wiki e página de teste que usamos. Quando quiser
adicionar mais páginas, é só colocar o título delas (com underscore no
lugar de espaço) na lista `"pages"`.

### 3. WhatsApp grátis via CallMeBot

1. Salva esse número nos seus contatos: **+34 644 51 95 23**
2. Manda a mensagem `I allow callmebot to add me` pra esse número no
   WhatsApp
3. Em alguns minutos ele te responde com uma **API Key**
4. Guarda seu número (formato internacional, ex: `5511999999999`) e a
   API Key

### 4. Email via Gmail

1. Ative a verificação em duas etapas na sua conta Google (se ainda não
   tiver)
2. Cria uma **senha de app** em
   https://myaccount.google.com/apppasswords
3. Guarda essa senha (é diferente da sua senha normal do Gmail)

### 5. Secrets no GitHub

No repositório: **Settings → Secrets and variables → Actions → New
repository secret**. Cria estes:

| Nome | Valor |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | seu email do Gmail |
| `SMTP_PASSWORD` | a senha de app que você gerou |
| `NOTIFY_EMAIL` | email que vai receber os avisos (pode ser o mesmo) |
| `CALLMEBOT_PHONE` | seu número, formato `5511999999999` |
| `CALLMEBOT_APIKEY` | a API key que o CallMeBot te mandou |

### 6. Rodar

O workflow já roda sozinho a cada 10 minutos. Pra testar na hora, vai em
**Actions → Fandom Watcher → Run workflow**.

## Sobre a primeira execução

Na primeira vez que rodar, o script só salva o estado atual como
"baseline" — não manda notificação de coisa que já existia antes. Só a
partir da segunda execução em diante que ele avisa do que for novo de
verdade.

## Limitações conhecidas

- A API do mural (`Fandom\MessageWall\MessageWall`) não é documentada
  oficialmente pela Fandom e pode mudar sem aviso.
- Comentários de artigo (não confundir com Message Wall) usam outra
  controller (`ArticleComments`) que não foi totalmente testada ainda —
  se a página que você quer monitorar tiver comentários de artigo em vez
  de mural, me avisa que a gente adapta.
- CallMeBot tem limite de tamanho de mensagem e pode ter instabilidade
  ocasional por ser um serviço gratuito de terceiros.
