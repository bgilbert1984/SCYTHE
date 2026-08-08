#!/usr/bin/env bash
set -euo pipefail

NDPI_VERSION="5.0"
NDPI_COMMIT="375f99ef9fb4999d778b57bbeece171b3fa9fba6"
NDPI_SHA256="7f77ac7f593e846c3a88d347250e8f232a5a26702d705c2d487a316ec16d2f4e"
NDPI_URL="https://codeload.github.com/ntop/nDPI/tar.gz/${NDPI_COMMIT}"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo; no changes were made." >&2
  exit 77
fi

source /etc/os-release
if [[ ${ID:-} != "almalinux" || ${VERSION_ID%%.*} != "10" ]]; then
  echo "This installer is restricted to AlmaLinux 10." >&2
  exit 64
fi

dnf install -y \
  nmap gcc make git curl gettext flex bison libtool autoconf automake \
  pkgconf-pkg-config libpcap-devel json-c-devel pcre2-devel libmaxminddb-devel libcap

build_root=$(mktemp -d /var/tmp/scythe-network-tools.XXXXXXXX)
cleanup() {
  if [[ -n ${build_root:-} && ${build_root} == /var/tmp/scythe-network-tools.* ]]; then
    rm -rf -- "${build_root}"
  fi
}
trap cleanup EXIT

archive="${build_root}/ndpi-${NDPI_VERSION}.tar.gz"
curl --fail --location --proto '=https' --tlsv1.2 "${NDPI_URL}" --output "${archive}"
printf '%s  %s\n' "${NDPI_SHA256}" "${archive}" | sha256sum --check --strict
tar --extract --gzip --file "${archive}" --directory "${build_root}"
source_dir="${build_root}/nDPI-${NDPI_COMMIT}"

cd "${source_dir}"
./autogen.sh
./configure --prefix=/usr/local --with-pcre2
make -j"$(getconf _NPROCESSORS_ONLN)"
if [[ ${SCYTHE_NDPI_RUN_CHECKS:-0} == "1" ]]; then
  make check
fi
make install
ldconfig
setcap 'cap_net_raw,cap_net_admin=eip' /usr/local/bin/ndpiReader
capabilities=$(getcap /usr/local/bin/ndpiReader)
if [[ ${capabilities} != *cap_net_raw* || ${capabilities} != *cap_net_admin* || ${capabilities} != *'=eip'* ]]; then
  echo "ndpiReader capture capabilities were not applied" >&2
  exit 1
fi

echo "Installed and verified:"
nmap --version | head -1
/usr/local/bin/ndpiReader -h 2>&1 | head -3
echo "nDPI source commit: ${NDPI_COMMIT}"
echo "Restart the SCYTHE instance to refresh its startup capability banner."
