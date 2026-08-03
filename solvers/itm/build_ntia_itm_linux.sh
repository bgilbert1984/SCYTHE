#!/usr/bin/env bash
set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source_revision=183ad95bd813a8be11009df396e1c631356864b2
source_tree_sha256=d655560f08c4720f4cec8b626c5f26388e8ca4cc63cb7bf48e6430507f7c0466
source_dir=$(mktemp -d /tmp/scythe-ntia-itm-source.XXXXXX)
trap 'rm -rf -- "$source_dir"' EXIT

for command in git g++ perl; do
  command -v "$command" >/dev/null || {
    echo "Required command is unavailable: $command" >&2
    exit 1
  }
done

git clone --quiet https://github.com/NTIA/itm.git "$source_dir"
git -C "$source_dir" checkout --quiet "$source_revision"
actual_tree_sha256=$(
  "$repository_root/.venv/bin/python" \
    "$repository_root/scripts/global_data/scythe_dataset_contract.py" \
    tree-hash "$source_dir"
)
if [[ "$actual_tree_sha256" != "$source_tree_sha256" ]]; then
  echo "NTIA ITM source-tree digest mismatch" >&2
  exit 1
fi

# The pinned upstream files use Windows include separators. This modifies only
# the disposable build copy; the verified clean-tree digest above remains the
# provenance digest recorded by the dataset contract.
perl -pi -e 's#\.\.\\include\\#../include/#g' "$source_dir"/src/*.cpp

build_dir="$repository_root/solvers/itm/build"
mkdir -p "$build_dir"
g++ -std=c++17 '-D__declspec(x)=' -I"$source_dir/include" \
  "$source_dir"/src/*.cpp \
  "$repository_root/solvers/itm/scythe_itm_area_driver.cpp" \
  -lm -o "$build_dir/scythe-itm-area"

printf '16\n' | "$build_dir/scythe-itm-area" \
  10 1 0 0 0 5 301 230 0 15 0.008 0 87
