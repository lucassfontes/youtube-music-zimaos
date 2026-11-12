# YouTube Music ZimaOS — v1.5.5 (GitHub + GHCR)

## Publicação no GitHub

Este pacote contém o código-fonte da v1.5.5 e o fluxo de publicação de imagem Docker no GitHub Container Registry (GHCR), para que o ZimaOS **não precise construir a imagem**. A imagem `ghcr.io/lucassfontes/youtube-music-zimaos:1.5.5` **só ficará disponível quando o repositório for publicado e a Action concluir**.

### Primeira publicação (no Linux, por exemplo Zorin OS)

Requisitos: `git` e `gh` (GitHub CLI). Se faltar: `sudo apt update && sudo apt install git gh`.

Execute na pasta extraída:

```bash
bash publicar-github.sh
```

O script pede autorização antes de criar um **repositório público**, abre o login GitHub (se necessário), cria `lucassfontes/youtube-music-zimaos`, envia o código e inicia a Action que publica a imagem GHCR. Nunca publica as pastas `dados/`, `musicas/`, `videos/` nem `.env`. Para enviar alterações futuras, execute novamente o mesmo script nessa pasta. O workflow também roda manualmente em **Actions > Publicar Docker no GHCR > Run workflow**.

### Tornar a imagem acessível para qualquer ZimaOS

O GitHub pode criar o pacote GHCR como **privado**, mesmo que o repositório seja público. Depois de uma Action concluída, abra a página de **Packages** da conta, escolha `youtube-music-zimaos`, entre em **Package settings > Change visibility** e marque **Public**. Faça isso apenas se quiser que a imagem Docker seja pública, como nos demais projetos.

### Instalação no ZimaOS (depois da imagem publicada)

```bash
cd /DATA
# Se ainda não tem a pasta do projeto:
git clone https://github.com/lucassfontes/youtube-music-zimaos.git
cd youtube-music-zimaos
bash iniciar.sh
```

O `iniciar.sh` continua aberto, pede `sudo` se necessário, mostra IP/porta e oferece menu de logs, reinício, parada e exportação do Compose do ZimaOS com o mesmo ícone do projeto. Ele baixa a **imagem pronta no GHCR**, sem construir `ffmpeg`/Python no servidor. O `compose.yaml` também pode ser importado no ZimaOS como aplicativo personalizado; o ícone é o `app/static/icon.png` do GitHub.

Se já tem dados de instalações ZIP anteriores, pare o container antigo e **copie o conteúdo de `dados/`, `musicas/`, `videos/` da pasta antiga para as mesmas subpastas do repositório**, mantendo backup antes de alterar. Evite subir duas versões simultâneas na porta `8787`.

Para atualizar depois de uma nova publicação: `git pull` dentro do repositório, seguido de `bash iniciar.sh` (a imagem é puxada automaticamente). Dados permanecem nas pastas do projeto e fora do Git.

### Alternativa sem GHCR (compilar localmente)

```bash
docker compose -f compose.yaml -f compose.local.yaml up -d --build
```

Esse comando volta a construir a imagem no servidor. Não é necessário quando o GHCR estiver publicado.

---


## Novidades — capa dentro do arquivo MP3

- Ao baixar um MP3, o servidor tenta obter a miniatura de vídeo fornecida pelo YouTube e a incorpora ao arquivo como **ID3 APIC (capa frontal)**, convertendo JPG, PNG ou WebP para JPEG compatível. Não salva apenas uma imagem ao lado do arquivo: **a capa vai dentro do MP3** e viaja com ele para outros players.
- **Downloads → Atualizar capa:** refaz a capa de uma música já baixada sem baixar o áudio novamente e sem perder artista, título ou álbum. **Adicionar capas antigas** processa em segundo plano todos os MP3s concluídos registrados no banco de dados que ainda estejam sem capa, mostrando atualizações, músicas ignoradas e falhas.
- A aba Downloads exibe as capas nos cards. O player inferior e os metadados enviados ao iPhone utilizam a capa incorporada quando a música já consta entre os downloads listados, com miniatura do YouTube como alternativa.
- Ao **Editar** título/cantor/álbum depois de baixar, a capa embutida é preservada.
- Quando o YouTube não oferece uma imagem acessível, o download MP3 ainda conclui normalmente e avisa que a capa não foi obtida. O botão **Atualizar capa** permite tentar novamente.
- **Limites:** a miniatura do vídeo nem sempre é a capa oficial do álbum. As capas antigas são reparadas apenas para downloads MP3 concluídos encontrados na aba Downloads/banco de dados; arquivos soltos sem registro não são identificados automaticamente. O Safari/iPhone decide se exibe a capa na tela bloqueada e pode limitar mídia em segundo plano. Teste real no ZimaOS é necessário.


Busca no YouTube com player **somente áudio**, download de músicas **MP3** e agora download de vídeos **MP4**, ambos pelo nome do canal do YouTube.

## Layout responsivo em celular — v1.5.5

- Navegação inferior fixa (**Buscar, Baixar por link, Downloads**) com ícones vetoriais e alvos maiores; em computadores permanece o menu lateral.
- Busca compacta, resultados com capa/título e **Tocar, MP3 e MP4** sempre acessíveis; rolagem acontece apenas na lista de resultados, sem levar o cabeçalho nem o player junto.
- Player menor e fixo acima da navegação inferior, com controles reais **Anterior, Play/Pause e Próxima**, mais barra de progresso arrastável. A música continua tocando ao trocar de aba.
- Downloads com lista rolável independente, ações **Editar, Atualizar capa, Cancelar e Excluir** adaptadas a telas estreitas; diálogos com rolagem e campos que não acionam zoom automático no Safari do iPhone.
- Área segura inferior (`safe-area-inset-bottom`) para aparelhos com indicador de gestos; telas de 320–430 px e celulares com orientação horizontal têm ajustes próprios.
- Mantém usuário e sessão, capas embutidas, streaming, MP3/MP4, ícone do projeto e armazenamento dentro das pastas `dados`, `musicas` e `videos`.

## O que mudou

- Nos resultados de busca há **Tocar**, **MP3** e **MP4**. MP3/MP4 iniciam a fila com **um clique**; a aba Downloads permite acompanhar, cancelar, editar, renomear ou excluir.
- Para vídeo, selecione **360p / 480p / 720p / 1080p**, padrão **até 720p**. Quando uma qualidade não existir, o yt-dlp tentará os formatos disponíveis até o limite selecionado; se nenhum funcionar, mostra o erro no download. A qualidade efetiva depende dos formatos oferecidos pelo YouTube.
- Em **Baixar por link**, os botões MP3 e MP4 usam os nomes conferidos e colocam na fila.
- Vídeos são armazenados em `videos/Nome do Canal/Título.mp4`, no projeto; músicas continuam em `musicas/Nome do Canal/Título.mp3`.
- Edição de MP4 concluído altera nome do arquivo, pasta e tags de título/artista/álbum **sem recodificar o vídeo**. MP3 continua com ID3/capa como antes.
- Um mesmo link pode ter **um MP3 e um MP4** baixados separadamente. Duplicatas do mesmo tipo não são sobrescritas.
- **Controles tradicionais:** ícones SVG para Play, Pause, Anterior, Próxima e volume. As barras animadas ficam discretas ao lado do estado de reprodução, sem substituir o botão.
- Player de áudio progressivo, login, painel lateral, rolagem independente, ícone do ZimaOS e menu de terminal permanecem.

## Estrutura persistente

```text
youtube-music-zimaos/
├── iniciar.sh                  # Menu interativo; pede acesso sudo se necessário
├── parar.sh
├── compose.yaml
├── Dockerfile
├── app/
├── dados/                      # Banco, sessões e cache
├── musicas/                    # Cantor / Faixa.mp3
└── videos/                     # Canal / Vídeo.mp4
```

## Melhorias do player na versão 1.5.5

- **Play e Pause tradicionais** com ícones SVG (triângulo / duas barras) no botão central, como em players de música profissionais. Sem emoji nem equalizador substituindo o controle.
- **Anterior / Próxima, volume, silenciar e fechar** também usam ícones vetoriais consistentes.
- **Indicador de barras separado**, discreto, ao lado do estado de reprodução; somente anima após o evento real `playing`.
- A busca mantém o botão de reprodução com ícones vetoriais; enquanto reproduz, ele vira **Pausar**.
- **Avançar ou voltar pela barra de progresso:** clique, arraste ou use as setas do teclado. A posição e o tempo aparecem enquanto arrasta. O salto só é solicitado ao soltar.
- Quando a música está salva no servidor, o salto é imediato via HTTP Range. Enquanto faz streaming, o player inicia uma nova transmissão a partir da posição escolhida; pode haver breve carregamento, dependendo do YouTube e da conexão.
- Áudios sem duração conhecida não habilitam busca até que a duração esteja disponível.

## Controles no celular (v1.5.5)

- Os botões laterais com **dois triângulos e uma barra** trocam para a faixa anterior ou próxima faixa, nunca avançam 10/15 segundos da faixa atual.
- O botão central é Play/Pause. Barra de progresso acima dos controles permite arrastar para um minuto específico, sem mudar de música.
- Botões ampliados para toque (48px ou mais), layout responsivo e suporte a área segura do iPhone.
- A sequência segue os resultados da busca. No último resultado carregado, Próxima tenta buscar mais resultados automaticamente; caso não haja mais, o botão fica indisponível.
- Ao fazer uma nova busca, a música atual continua com sua lista anterior até você selecionar outra faixa.

## Controles de música na tela bloqueada do iPhone (v1.5.5)

- O aplicativo registra no **Media Session API** as ações `previoustrack` e `nexttrack` **após o evento real de reprodução `playing`**, como exigido por algumas versões do Safari no iOS.
- Remove os handlers `seekbackward` e `seekforward` (os comandos de pular 10 segundos), pois eles podem prevalecer sobre troca de faixas nos controles do iPhone.
- Envia título, cantor/canal, capa e posição da faixa para a Central de Controle / tela bloqueada quando o navegador aceitar esses dados.
- Muda de faixa mantendo o mesmo elemento de áudio, sem fechar e recriar a sessão do player; Play/Pause também recebem comandos do sistema.
- O avanço pela barra dentro do aplicativo continua igual. Quando o iOS oferecer a barra de progresso, o evento `seekto` usa o mesmo mecanismo de avanço.
- **Limitação:** o Safari/iOS controla a interface nativa. Não é possível garantir que todas as versões do iOS mostrem ou executem Anterior/Próxima, nem que streams MP3 sem duração de cabeçalho apresentem o tempo correto na tela bloqueada. Precisa de teste em iPhone real; confirme que existe mais de uma música na busca.

## Instalar no ZimaOS

Depois de criar o repositório e concluir a Action do GHCR, instale com `git clone` conforme as instruções acima. Execute `bash iniciar.sh` dentro da pasta clonada. O terminal mostra a URL (porta 8787) e mantém o menu aberto; a imagem é baixada pronta do GHCR, sem compilação no ZimaOS.

### Atualizar uma versão anterior

1. Pare a versão anterior, sem remover volumes ou apagar pastas.
2. Faça backup de **`dados/` e `musicas/`**.
3. Extraia a nova pasta, copie o **conteúdo** de `dados/` e `musicas/` da versão anterior para as pastas correspondentes da versão 1.5.5.
4. Inicie `bash iniciar.sh`. O banco é migrado automaticamente e seus downloads anteriores continuarão sendo MP3.
5. A partir de agora, preserve também **`videos/`** nas futuras atualizações.

Se já existe uma instalação da versão 1.5.5, faça backup das **três** pastas antes de substituir arquivos.

## Limites

- A extração depende do YouTube e pode retornar 403/429, restrição regional ou formatos indisponíveis. O vídeo é baixado **como MP4** usando FFmpeg para reunir áudio e vídeo quando necessário. O servidor **não reproduz vídeos** no player (somente áudio, conforme a preferência anterior).
- A seleção de qualidade define um **limite máximo**, não garante exatamente aquela resolução. A preferência é usar formatos MP4+M4A nativos para evitar recodificação pesada.
- MP4s maiores precisam de espaço livre em `dados/work` **e** na pasta `videos/` durante a finalização. Um HD lento pode demorar para copiar arquivos.
- Cancelamento durante finalização pode levar alguns segundos.
- Não disponibilize a porta diretamente na internet; prefira LAN/Tailscale. Baixe apenas materiais para os quais possui autorização.

## Verificações locais

```bash
python -m unittest discover -s tests -v
node --check app/static/app.js
bash -n iniciar.sh parar.sh
```

A busca e os downloads reais dependem de rede/YouTube. Testes locais são simulados e não garantem disponibilidade de vídeo/áudio.
