#!/usr/bin/env bash
# Alias compatível com instruções da versão anterior.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1
exec bash ./iniciar.sh "$@"
