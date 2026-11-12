# YouTube Music ZimaOS — v1.5.6

Aplicativo web com busca no YouTube, player somente de áudio, downloads MP3/MP4, capas embutidas no MP3, fila com edição/cancelamento/exclusão e interface responsiva. O cantor é sugerido pelo **nome do canal**, podendo ser corrigido na aba Downloads. Use somente conteúdo que você tenha direito de baixar.

## Novidades 1.5.6

- A checagem de duplicidade verifica a existência **real** do MP3/MP4 no disco, não apenas o status salvo no SQLite.
- Quando o registro dizia `concluido` mas o arquivo não está presente, ele vira `ausente` e o download pode ser repetido.
- Se o arquivo existe, uma janela própria oferece **Substituir**, **Manter os dois** ou **Cancelar**.
- **Substituir**: o novo conteúdo só substitui o anterior depois que o download, a conversão e a preparação do arquivo terminam. Erro ou cancelamento antes disso preserva o original.
- **Manter os dois**: grava um novo nome `Música (2).mp3` / `Vídeo (2).mp4`, depois `(3)` e assim por diante. O original não é alterado.
- Downloads em andamento não são duplicados enquanto estão na fila.
- A aba Downloads indica quando um arquivo foi apagado externamente.

## Organização de pastas no ZimaOS

```text
/DATA/AppData/YouTube_Music/
├── Musica/
│   └── Nome do Canal/
│       └── Nome da Música.mp3
├── Videos/
│   └── Nome do Canal/
│       └── Nome do Vídeo.mp4
└── dados/
    ├── app.db
    └── cache e arquivos temporários
```

Os volumes mapeiam `Musica` para `/music`, `Videos` para `/videos` e `dados` para `/config` no container. O Compose inclui o serviço **preparar-pastas**, que cria essas três pastas e prepara a escrita do UID 1000, sem alterar recursivamente a biblioteca de mídia. Não usa volumes anônimos que desapareceriam ao remover o container.

### Instalar como aplicativo personalizado

O arquivo `zimaos-compose.example.yaml` contém o Compose completo com caminhos absolutos e o ícone do projeto. **Importante:** a tag `ghcr.io/lucassfontes/youtube-music-zimaos:1.5.6` só estará disponível depois de enviar esta versão ao GitHub e concluir a Action de publicação.

Se a imagem GHCR já estiver pública e publicada, importe o YAML na seção de aplicativos personalizados do ZimaOS. Antes de importar, pare/remova o container da versão anterior de mesmo nome `youtube-music-zimaos` para evitar conflito de nomes. Remover somente o container **não apaga** os arquivos das pastas montadas, mas faça backup antes de alterar os volumes.

### Alternativa: construir localmente usando o código deste ZIP

```bash
cd /DATA/youtube-music-zimaos
bash criar-pastas-zimaos.sh
# Quando a imagem 1.5.6 ainda não está publicada no GHCR:
docker compose -f compose.yaml -f compose.local.yaml up -d --build
```

Para iniciar pelo menu do terminal, depois que a imagem for publicada no GHCR, use `bash iniciar.sh`. O inicializador verifica permissões, mostra IP/porta e permanece aberto com opções de logs, reinício, parada e exportação de Compose.

### Atualizar GitHub e imagem GHCR

Na sua máquina com Git e GitHub CLI (`gh`) autenticados, na pasta extraída desta versão, execute `bash publicar-github.sh`. O script envia código para `lucassfontes/youtube-music-zimaos`; a Action `.github/workflows/publish-ghcr.yml` compila a nova imagem `1.5.6` e atualiza `latest`. Os arquivos da biblioteca e `.env` não são enviados ao Git. Verifique o resultado em GitHub > Actions antes de instalar. O arquivo `compose.local.yaml` é apenas a alternativa para compilação local.

## Migração segura — instalações anteriores

**Não apague as pastas anteriores.** Pare a versão antiga e faça um backup antes de copiar as músicas, vídeos ou o banco de dados. Se o usuário e os downloads já estão em `/DATA/AppData/YouTubeMusicZimaOS`, execute no servidor:

```bash
bash migrar-biblioteca.sh /DATA/AppData/YouTubeMusicZimaOS
```

O script pede confirmação e copia `dados` -> `dados`, `musicas` -> `Musica`, `videos` -> `Videos` sem sobrescrever arquivos já existentes. Se sua biblioteca antiga estiver em outro local, informe o caminho correto em vez do exemplo. Só ligue o novo container após conferir que `dados/app.db` e a mídia foram copiados. Para arquivos de mídia já existentes em subpastas pertencentes a outro UID, pode ser necessário ajustar permissões específicas desses diretórios.

## Limitações e validação

As validações locais incluem testes simulados de download, substituição segura, duplicação, acesso, progressão e interface. Ainda não comprovam que um download ou streaming real funcione com todos os vídeos do YouTube ou que a tela bloqueada do Safari mostre os controles desejados. O YouTube pode bloquear vídeos ou streams. A substituição e a criação de cópias são decisões explícitas do usuário, e o aplicativo deve ficar restrito à rede local/VPN; não exponha a porta 8787 diretamente à internet.
