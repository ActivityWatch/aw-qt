#!/bin/bash

set -e

echo "Running pyrcc5 to build resources.py file..."
pyrcc5 -o aw_qt/resources.py aw_qt/resources.qrc
echo "Done!"