#!/usr/bin/env bash
# Cria o atalho "Consulta de Processos" no menu de aplicativos e na área de trabalho.
cd "$(dirname "$0")" || exit 1
DIR="$PWD"
chmod +x "$DIR/Consultar-Processos.sh"
DESK_FILE="$(cat <<DESK
[Desktop Entry]
Type=Application
Name=Consulta de Processos
Comment=Dossiê judicial por CPF ou CNPJ (fontes oficiais)
Exec="$DIR/Consultar-Processos.sh"
Path=$DIR
Icon=$DIR/consulta_processos/icone.svg
Terminal=false
Categories=Office;Utility;
DESK
)"
mkdir -p "$HOME/.local/share/applications"
echo "$DESK_FILE" > "$HOME/.local/share/applications/consulta-processos.desktop"
chmod +x "$HOME/.local/share/applications/consulta-processos.desktop"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP_DIR" ]; then
  echo "$DESK_FILE" > "$DESKTOP_DIR/consulta-processos.desktop"
  chmod +x "$DESKTOP_DIR/consulta-processos.desktop"
  command -v gio >/dev/null && gio set "$DESKTOP_DIR/consulta-processos.desktop" metadata::trusted true 2>/dev/null
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
echo "Atalho criado no menu de aplicativos${DESKTOP_DIR:+ e em $DESKTOP_DIR}."
