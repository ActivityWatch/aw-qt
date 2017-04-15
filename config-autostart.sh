#!/bin/bash

if [ -z $XDG_CONFIG_HOME ]; then
    # XDG_CONFIG_HOME not set
    AUTOSTART_PATH=$HOME/.config/autostart
else
    AUTOSTART_PATH=$XDG_CONFIG_HOME/autostart
fi

cp aw-qt.desktop $AUTOSTART_PATH
