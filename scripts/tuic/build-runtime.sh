#!/usr/bin/env bash
# Run on a trusted builder with >= 4GiB free disk and >= 2GiB available memory.
# Does not install software or touch any service. Argument must be a new directory.
set -euo pipefail
build_dir="${1:?usage: build-runtime.sh NEW_OUTPUT_DIRECTORY}"
[[ ! -e "$build_dir" ]] || { echo 'Output directory already exists' >&2; exit 2; }
[[ "$(go env GOVERSION)" == 'go1.26.8' ]] || { echo 'Provision trusted Go1.26.8 first' >&2; exit 2; }
mkdir -m 700 -p "$build_dir"
build_dir="$(cd "$build_dir" && pwd)"
export GOTOOLCHAIN=local CGO_ENABLED=0 GOOS=linux GOARCH=amd64
export GOMAXPROCS=1 GOMEMLIMIT=512MiB
export GOPATH="$build_dir/go" GOCACHE="$build_dir/cache" TMPDIR="$build_dir/tmp"
mkdir "$TMPDIR"
curl --fail --location --proto '=https' --tlsv1.2 \
  'https://github.com/SagerNet/sing-box/archive/refs/tags/v1.14.2.tar.gz' \
  --output "$build_dir/source.tar.gz"
printf '%s  %s\n' '67dd8f8c37ecaaadcfcafad1f0827eed4b034c963b86fd3aa5c0d7a36876845d' "$build_dir/source.tar.gz" | sha256sum --check -
tar --extract --gzip --file "$build_dir/source.tar.gz" --directory "$build_dir"
cd "$build_dir/sing-box-1.14.2"
# Default module checksum validation remains enabled. Download only build deps.
go build -mod=readonly -trimpath -p=1 -tags 'with_quic,with_v2ray_api' \
  -ldflags '-s -w -X github.com/sagernet/sing-box/constant.Version=1.14.2' \
  -o "$build_dir/sing-box" ./cmd/sing-box
"$build_dir/sing-box" version > "$build_dir/version.txt"
go version -m "$build_dir/sing-box" > "$build_dir/go-build-info.txt"
sha256sum "$build_dir/sing-box" "$build_dir/source.tar.gz" go.mod go.sum > "$build_dir/SHA256SUMS"
printf '%s\n' 'Build only: run isolated runtime-gate.py before accepting this artifact.'
