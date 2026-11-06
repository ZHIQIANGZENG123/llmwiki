#!/usr/bin/env bash
# Offline install of LibreOffice + CJK fonts + a JRE on Debian 12.
#
# LLM Wiki converts .docx/.pptx to PDF with LibreOffice, then extracts text from the
# PDF with Java. Neither ships with Debian, and an air-gapped machine cannot apt-install
# them, so this directory carries everything needed.
#
#   sudo ./install-office-offline.sh            install
#   ./install-office-offline.sh --check         verify only (no root needed)
#
# Layout expected next to this script:
#   LibreOffice_*_Linux_x86-64_deb.tar.gz     official archive, contains DEBS/*.deb
#   fonts/*.deb                               CJK fonts (wqy-zenhei and/or noto-cjk)
#   jre/OpenJDK*jre*x64*linux*.tar.gz         portable JRE (no dpkg needed)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-/opt/llmwiki}"
JRE_DIR="$PREFIX/jre"

step() { printf "\n== %s ==\n" "$1"; }
ok()   { printf "  [ok]   %s\n" "$1"; }
warn() { printf "  [warn] %s\n" "$1"; }
die()  { printf "  [fail] %s\n" "$1" >&2; exit 1; }

check_only=0
[[ "${1:-}" == "--check" ]] && check_only=1

verify() {
  step "验证"
  if command -v soffice >/dev/null 2>&1; then
    ok "soffice $(soffice --version 2>/dev/null | head -1)"
  else
    warn "soffice 未找到 —— Word/PPT 无法解析"
  fi

  local java_bin="$JRE_DIR/bin/java"
  command -v java >/dev/null 2>&1 && java_bin="$(command -v java)"
  if [[ -x "$java_bin" ]]; then
    ok "java $("$java_bin" -version 2>&1 | head -1)"
  else
    warn "java 未找到 —— PDF 文本无法提取"
  fi

  local cjk=""
  if command -v fc-list >/dev/null 2>&1; then
    cjk="$(fc-list :lang=zh 2>/dev/null | wc -l || true)"
  fi
  if [[ -z "$cjk" ]]; then
    warn "无法检查中文字体（fontconfig 未安装）"
  elif [[ "$cjk" -gt 0 ]]; then
    ok "中文字体 $cjk 个"
  else
    warn "没有中文字体 —— 中文文档转 PDF 会是空白/方框"
  fi
}

if [[ "$check_only" -eq 1 ]]; then
  verify
  exit 0
fi

[[ $EUID -eq 0 ]] || die "需要 root：sudo $0"

# ------------------------------------------------------- system libraries
step "系统运行库"
shopt -s nullglob
sysdebs=("$HERE"/debsystem/*.deb)
shopt -u nullglob
if [[ ${#sysdebs[@]} -eq 0 ]]; then
  warn "未找到系统库包，跳过（LibreOffice 可能缺 .so 无法启动）"
else
  installed=0
  skipped=0
  failed=()
  for deb in "${sysdebs[@]}"; do
    pkg="$(dpkg-deb -f "$deb" Package 2>/dev/null || true)"
    [[ -z "$pkg" ]] && continue
    # Never touch a package the base system already provides: dpkg installs whatever
    # version you hand it, downgrade included, and that is not something to do blind.
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
      skipped=$((skipped + 1))
      continue
    fi
    if dpkg -i "$deb" >/dev/null 2>&1; then
      installed=$((installed + 1))
    else
      failed+=("$pkg")
    fi
  done
  ok "新装 ${installed} 个，已有 ${skipped} 个"
  [[ ${#failed[@]} -gt 0 ]] && warn "安装失败：${failed[*]}"
fi

# ---------------------------------------------------------------- LibreOffice
step "LibreOffice"
tarball="$(ls "$HERE"/LibreOffice_*_Linux_x86-64_deb.tar.gz 2>/dev/null | head -1 || true)"
if [[ -z "$tarball" ]]; then
  warn "未找到 LibreOffice 归档，跳过"
elif command -v soffice >/dev/null 2>&1; then
  ok "已安装，跳过"
else
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  tar -xzf "$tarball" -C "$work"
  # The archive is self-contained; if a system library is missing dpkg reports it and we
  # surface the list instead of leaving a half-configured install behind.
  if dpkg -i "$work"/*/DEBS/*.deb >/tmp/llmwiki-lo-install.log 2>&1; then
    ok "安装完成"
  else
    warn "dpkg 报告依赖问题，见 /tmp/llmwiki-lo-install.log"
    warn "在联网的 Debian 12 上运行 tools/make-office-bundle.sh 取全依赖后重装"
  fi
fi

# ---------------------------------------------------------------- CJK fonts
step "中文字体"
shopt -s nullglob
font_debs=("$HERE"/fonts/*.deb)
shopt -u nullglob
if [[ ${#font_debs[@]} -eq 0 ]]; then
  warn "未找到字体包，跳过"
else
  # Two versions of the same package must not be installed in one dpkg run.
  for deb in "${font_debs[@]}"; do
    if dpkg -i "$deb" >/dev/null 2>&1; then
      ok "$(basename "$deb")"
    else
      warn "$(basename "$deb") 失败（可能已装过更高版本）"
    fi
  done
  fc-cache -f >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------- JRE
step "JRE"
jre_tar="$(ls "$HERE"/jre/OpenJDK*jre*x64*linux*.tar.gz 2>/dev/null | head -1 || true)"
if [[ -z "$jre_tar" ]]; then
  warn "未找到 JRE 归档，跳过"
elif [[ -x "$JRE_DIR/bin/java" ]]; then
  ok "已安装在 $JRE_DIR"
else
  mkdir -p "$PREFIX"
  tar -xzf "$jre_tar" -C "$PREFIX"
  extracted="$(find "$PREFIX" -maxdepth 1 -type d \( -name 'jdk-*' -o -name 'jre-*' \) | head -1)"
  if [[ -n "$extracted" ]]; then
    ln -sfn "$extracted" "$JRE_DIR"
  fi
  ok "安装到 $JRE_DIR"
fi

verify

cat <<EOF

下一步：让服务能找到它们
  systemd 单元里加：
    Environment=PATH=$JRE_DIR/bin:/usr/bin:/bin
    Environment=HOME=/var/lib/llmwiki

  非 systemd 启动：
    export PATH=$JRE_DIR/bin:\$PATH

  自检：
    $0 --check
EOF
