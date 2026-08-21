#!/bin/bash
# Fails a Release build whose API address is not a real, secure, remote origin.
#
# This exists because the failure it prevents is silent: an app that ships
# pointing at 192.168.1.227 builds cleanly, passes review's automated checks,
# installs fine, and then times out for every user who is not on the
# developer's Wi-Fi. A build-time refusal is the only place to catch that
# before it reaches anybody.
#
# Debug builds are skipped entirely — pointing at a laptop is the whole point
# of a Debug build.
set -euo pipefail

if [ "${CONFIGURATION}" != "Release" ]; then
    echo "note: API config validation skipped for ${CONFIGURATION}"
    exit 0
fi

# Read the SOURCE plist, not the built one.
#
# The built copy is a derived artifact: on an incremental build Xcode may not
# regenerate it, so validating it can pass against a *stale* value. That is not
# hypothetical — it was caught doing exactly that, reporting a previously
# validated https origin while the source had been changed to a LAN address.
# The source file is the truth and is never stale.
PLIST="${SRCROOT}/${INFOPLIST_FILE}"
if [ ! -f "${PLIST}" ]; then
    echo "error: Info plist not found at ${PLIST}"
    exit 1
fi

URL=$(/usr/libexec/PlistBuddy -c "Print :SAVA_API_BASE_URL" "${PLIST}" 2>/dev/null || echo "")

if [ -z "${URL}" ]; then
    echo "error: SAVA_API_BASE_URL is missing from Info-Release.plist."
    exit 1
fi

if [ "${URL}" = "REPLACE_WITH_PRODUCTION_API_URL" ]; then
    echo "error: SAVA_API_BASE_URL is still the placeholder."
    echo "error: Set Sava's deployed HTTPS origin in ios/Info-Release.plist before building for Release."
    exit 1
fi

case "${URL}" in
    https://*) ;;
    *)
        echo "error: SAVA_API_BASE_URL must use https. Got: ${URL}"
        exit 1
        ;;
esac

HOST=$(echo "${URL}" | sed -E 's#^https://##; s#[:/].*$##')

case "${HOST}" in
    localhost|*.local|127.*|10.*|192.168.*|169.254.*|::1)
        echo "error: SAVA_API_BASE_URL points at the private address ${HOST}."
        echo "error: A Release build must target a publicly reachable host."
        exit 1
        ;;
    172.1[6-9].*|172.2[0-9].*|172.3[0-1].*)
        echo "error: SAVA_API_BASE_URL points at the private address ${HOST}."
        exit 1
        ;;
esac

# An ATS exception in a shipping build is almost always an accident.
if /usr/libexec/PlistBuddy -c "Print :NSAppTransportSecurity" "${PLIST}" >/dev/null 2>&1; then
    echo "error: Info-Release.plist declares NSAppTransportSecurity."
    echo "error: Release builds must run under full ATS with no exceptions."
    exit 1
fi

echo "note: API configuration validated — ${URL}"
