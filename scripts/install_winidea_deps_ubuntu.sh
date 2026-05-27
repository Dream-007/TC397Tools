#!/usr/bin/env bash
set -euo pipefail

# Installs OS-level dependencies required by winIDEA Linux portable packages.
# This follows TASKING/iSYSTEM's Ubuntu/Debian guidance and requires sudo.

. /etc/lsb-release

sudo apt update
if ! strings /lib/x86_64-linux-gnu/libstdc++.so.6 | grep -q 'GLIBCXX_3.4.32'; then
  sudo add-apt-repository --yes ppa:ubuntu-toolchain-r/test
  sudo apt update
  sudo apt install --yes --only-upgrade libstdc++6
fi

sudo dpkg --add-architecture i386
sudo mkdir -pm755 /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/winehq-archive.key ]; then
  wget -O - https://dl.winehq.org/wine-builds/winehq.key \
    | sudo gpg --dearmor -o /etc/apt/keyrings/winehq-archive.key -
fi
sudo wget -NP /etc/apt/sources.list.d/ \
  "https://dl.winehq.org/wine-builds/ubuntu/dists/${DISTRIB_CODENAME}/winehq-${DISTRIB_CODENAME}.sources"

TASKING_URL="https://www.isystem.com/downloads/linux/debian"
wget -O - "${TASKING_URL}/tasking.asc" \
  | sudo gpg --dearmor --yes -o /etc/apt/trusted.gpg.d/tasking.gpg
sudo wget -NP /etc/apt/sources.list.d/ "${TASKING_URL}/tasking.sources"

sudo apt update
sudo apt install --yes --install-recommends winehq-stable trte xvfb

echo "winIDEA Linux dependencies installed."
