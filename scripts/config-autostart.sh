#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$SCRIPT_DIR/.."

if [ -z $XDG_CONFIG_HOME ]; then
    # XDG_CONFIG_HOME not set
    AUTOSTART_PATH=$HOME/.config/autostart
else
    AUTOSTART_PATH=$XDG_CONFIG_HOME/autostart
fi

echo "Installing .desktop file to $AUTOSTART_PATH"

cp $ROOT_DIR/resources/aw-qt.desktop $AUTOSTART_PATH
xdg-icon-resource install --novendor --size 32 $ROOT_DIR/img/logo.png activitywatch
xdg-icon-resource install --novendor --size 512 $ROOT_DIR/img/logo.png activitywatch
