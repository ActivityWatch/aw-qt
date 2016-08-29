#!/bin/bash

function fail() {
    echo "Failed running last command"
    exit 1
}

echo "Running pyrcc5 to build resources.py file"
pyrcc5 -o aw_qt/resources.py aw_qt/resources.qrc || fail
