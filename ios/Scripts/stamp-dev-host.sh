#!/bin/bash
# Debug only. Stamps this Mac's CURRENT LAN address into the built Info.plist.
#
# Why this exists: a Debug build on a physical phone cannot use `localhost` —
# on the device that means the device. It has to be the Mac's LAN IP. But
# hardcoding that IP is exactly what broke before: DHCP reassigned the Mac from
# 192.168.1.227 to 192.168.1.75 and every build kept pointing at an address
# nothing answered on.
#
# So the address is not stored anywhere. It is read from the machine at build
# time, which means it is correct after switching Wi-Fi, rejoining a network, or
# any lease renewal — without editing a tracked file.
set -euo pipefail

if [ "${CONFIGURATION}" != "Debug" ]; then
    exit 0
fi

PLIST="${BUILT_PRODUCTS_DIR}/${INFOPLIST_PATH}"
[ -f "${PLIST}" ] || exit 0

# en0 is Wi-Fi on every Mac laptop; en1 covers Ethernet/Thunderbolt adapters.
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"

if [ -z "${LAN_IP}" ]; then
    echo "warning: no LAN address found — a Debug build on a physical device will not reach this Mac."
    exit 0
fi

PORT="${SAVA_DEV_PORT:-8000}"
URL="http://${LAN_IP}:${PORT}"

/usr/libexec/PlistBuddy -c "Delete :SAVA_DEV_LAN_URL" "${PLIST}" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Add :SAVA_DEV_LAN_URL string ${URL}" "${PLIST}"

echo "note: device builds will use ${URL}"
