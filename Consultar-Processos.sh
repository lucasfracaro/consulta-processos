#!/usr/bin/env bash
# Abre a tela da Consulta de Processos (Linux/Mac). Usa só o Python do sistema, sem instalar pacotes.
cd "$(dirname "$0")" || exit 1
avisar() {
  echo "$1"
  command -v notify-send >/dev/null && notify-send "Consulta de Processos" "$1"
  command -v zenity >/dev/null && zenity --error --text="$1" 2>/dev/null
}
PY="$(command -v python3 || command -v python)"
[ -z "$PY" ] && { avisar "Python 3 não encontrado. Instale o Python 3 (no Arch/CachyOS: sudo pacman -S python)."; exit 1; }
exec "$PY" -m consulta_processos.gui >>"$PWD/consulta.log" 2>&1
