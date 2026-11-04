#!/bin/bash
# Deterministic lossless archive of RERUN2 + manifest + verification. Source dir is read-only and never modified.
set -euo pipefail
PARENT=/home/spectrcyde; RUN=catalogue_xtr_20261103_rerun2
OUT=$HOME/exp_e_evidence/${RUN}_archive; mkdir -p "$OUT"; cd "$PARENT"
echo "[1] manifest of untouched source"; date -u +%FT%TZ
( cd "$PARENT" && find "$RUN" -printf '%y %m %s %p\n' | LC_ALL=C sort -k4 > "$OUT/tree.txt" )
( cd "$PARENT" && find "$RUN" -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$OUT/files.sha256" )
wc -l "$OUT/tree.txt" "$OUT/files.sha256" | head -2
echo "[2] deterministic tar | sha256 | zstd -6"
tar --sort=name --mtime='UTC 2026-11-03 00:00:00' --owner=0 --group=0 --numeric-owner --format=posix \
    --pax-option=delete=atime,delete=ctime -C "$PARENT" -cf - "$RUN" \
  | tee >(sha256sum | cut -d' ' -f1 > "$OUT/tar.sha256") \
  | zstd -T0 -6 -q -o "$OUT/$RUN.tar.zst" --force
sleep 2
echo "[3] verify"
zstd -t -q "$OUT/$RUN.tar.zst" && echo "zstd -t OK"
sha256sum "$OUT/$RUN.tar.zst" | cut -d' ' -f1 > "$OUT/zst.sha256"
D=$(zstd -dc "$OUT/$RUN.tar.zst" | sha256sum | cut -d' ' -f1)
[ "$D" = "$(cat "$OUT/tar.sha256")" ] && echo "decompressed tar hash == tar hash" || { echo "MISMATCH tar"; exit 1; }
echo "[4] extract to scratch and compare every file + tree"
V=$HOME/exp_e_evidence/_verify_$$; mkdir -p "$V"; zstd -dc "$OUT/$RUN.tar.zst" | tar -C "$V" -xf -
( cd "$V" && find "$RUN" -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum ) | diff - "$OUT/files.sha256" && echo "all $(wc -l < "$OUT/files.sha256") file hashes match"
( cd "$V" && find "$RUN" -printf '%y %s %p\n' | LC_ALL=C sort -k3 ) | diff - <(awk '{print $1,$3,$4}' "$OUT/tree.txt") && echo "tree (types/sizes/paths) matches"
rm -rf "$V"
echo "[5] result"; ls -l "$OUT"; du -sh "$PARENT/$RUN"; cat "$OUT/tar.sha256" "$OUT/zst.sha256"
date -u +%FT%TZ; echo DONE
