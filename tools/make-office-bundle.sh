#!/usr/bin/env bash
# Build a COMPLETE offline package set for Office parsing on Debian 12.
#
# Run this on a *connected* Debian 12 machine (same release and CPU architecture as the
# target). It downloads every .deb apt would install — including transitive dependencies —
# so the offline machine never needs a network or a package source.
#
#   ./make-office-bundle.sh [output-dir]
#
# Result:
#   <output-dir>/DEBS/*.deb      full dependency closure
#   <output-dir>/Packages.gz     local apt index
#   <output-dir>/INSTALL.txt     how to install on the offline machine
set -euo pipefail

OUT="${1:-./office-debs}"
[[ $EUID -eq 0 ]] || { echo "需要 root（apt 下载到 /var/cache）：sudo $0 $*" >&2; exit 1; }

. /etc/os-release
if [[ "${ID:-}" != "debian" || "${VERSION_ID:-}" != "12" ]]; then
  echo "警告：当前是 ${PRETTY_NAME:-unknown}，目标必须是 Debian 12（bookworm）" >&2
  echo "      版本不一致会导致依赖对不上，离线机装不上。" >&2
  read -r -p "仍然继续？[y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || exit 1
fi

echo "== 清理缓存目录 =="
rm -rf /var/cache/apt/archives/*.deb

echo "== 下载依赖闭包 =="
apt-get update
# --download-only resolves the closure but installs nothing.
apt-get install --download-only -y \
  libreoffice-writer \
  libreoffice-impress \
  default-jre-headless \
  fonts-wqy-zenhei \
  fonts-noto-cjk

echo "== 收集 .deb =="
mkdir -p "$OUT/DEBS"
cp /var/cache/apt/archives/*.deb "$OUT/DEBS"/
count=$(find "$OUT/DEBS" -name '*.deb' | wc -l)
size=$(du -sh "$OUT/DEBS" | cut -f1)
echo "  $count 个包，共 $size"

echo "== 生成本地 apt 索引 =="
( cd "$OUT/DEBS" && dpkg-scanpackages . /dev/null > ../Packages && gzip -kf ../Packages )

cat > "$OUT/INSTALL.txt" <<'EOF'
离线安装（在目标 Debian 12 上，需要 root）：

  tar -xzf office-debs.tar.gz -C /opt
  echo "deb [trusted=yes] file:///opt/office-debs/DEBS ./" > /etc/apt/sources.list.d/llmwiki-office.list
  apt-get update
  apt-get install -y libreoffice-writer libreoffice-impress default-jre-headless fonts-wqy-zenhei

走本地 apt 源而不是 dpkg -i，是因为 apt 会按顺序解析依赖；
dpkg -i 遇到缺依赖只会失败，离线机上没法补救。

验证：
  soffice --version
  java -version
  fc-list :lang=zh | wc -l      # 必须 > 0，否则中文文档会转成空白
EOF

echo
echo "完成：$OUT"
echo "打包搬运： tar -czf office-debs.tar.gz -C $(dirname "$OUT") $(basename "$OUT")"
