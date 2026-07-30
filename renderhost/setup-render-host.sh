#!/usr/bin/env bash
# Bootstraps a fresh Linux machine to run the renderhost/ prototype,
# following ARCHITECTURE.md §2-§4's own already-vetted specifics.
#
# ============================================================================
# UNTESTED ON REAL RENDER-HOST HARDWARE. Written from the architecture doc's
# specifics plus standard Debian/Ubuntu practice — this development session
# has no RX 580 / 5-output AMD card to actually run it against. Read every
# section before running. The riskiest parts (static network config, a
# systemd service) are only ever WRITTEN to files for you to review — never
# applied automatically. Never run this against a machine you can't recover
# via physical access if the network step goes wrong (ARCHITECTURE.md §3's
# own warning: "Never ship a netplan change to the host without a tested
# rollback").
# ============================================================================
#
# Usage (from the repo root, or anywhere — the script finds itself):
#   ./renderhost/setup-render-host.sh              install + verify only
#   ./renderhost/setup-render-host.sh --gen-configs   also write netplan/systemd
#                                                      templates under ./renderhost/host-configs/
#                                                      (for review — nothing here is applied)
#
# What this does NOT do, on purpose — these are venue/hardware-specific,
# physical, or destructive, and don't belong in an unattended script:
#   - Apply the generated netplan config (§3) — review it, adjust the
#     interface name, THEN apply it yourself, with a rollback plan.
#   - Install/enable the generated systemd service (§10) — review it first.
#   - Compose real projector outputs with xrandr (§4) — this depends on
#     which connectors are actually populated at THIS venue; the script
#     prints the query command and an example, not a fixed command.
#   - Any BIOS setting (e.g. "iGPU Multi-Monitor" for a 5th head, §2).
#   - Physical cabling, DP++ verification (§7).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$REPO_ROOT/.venv"
GEN_CONFIGS=0
[[ "${1:-}" == "--gen-configs" ]] && GEN_CONFIGS=1

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m!   %s\033[0m\n' "$1"; }
die()  { printf '\033[1;31mx   %s\033[0m\n' "$1"; exit 1; }

# --- preflight ---------------------------------------------------------
log "Preflight checks"
[[ "$(uname -s)" == "Linux" ]] || die "This targets Linux (the doc assumes amdgpu/X11/KMSDRM) — got $(uname -s)."
command -v apt-get >/dev/null 2>&1 || die "This script assumes a Debian/Ubuntu-family host (apt-get). Adapt the package list for anything else."
echo "OK — Linux, apt-based."

# --- system packages -----------------------------------------------------
log "Installing system packages (GPU/GL, SDL2, X11 tools, Python, ssh)"
sudo apt-get update
sudo apt-get install -y \
  mesa-utils libgl1-mesa-dri libegl1-mesa \
  libsdl2-2.0-0 libsdl2-dev \
  x11-xserver-utils \
  python3 python3-venv python3-pip \
  openssh-server git curl
# amdgpu itself is in-kernel (§2's whole rationale for choosing AMD) — no
# driver package to install here; this just confirms the module is present.
if lsmod | grep -q amdgpu; then
  echo "amdgpu kernel module: loaded"
else
  warn "amdgpu kernel module not currently loaded — fine if the card isn't installed yet, worth checking (lsmod | grep amdgpu) once it is."
fi

# --- Python environment ----------------------------------------------------
log "Setting up the Python venv and renderhost/ dependencies"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
  echo "created $VENV_DIR"
else
  echo "$VENV_DIR already exists — reusing it"
fi
# Use the venv's own python3 -m pip explicitly, not bare `pip` — this repo
# has previously had .venv/bin/python3 and .venv/bin/pip resolve to
# DIFFERENT Python versions inside the same venv (a pre-existing, unrelated
# inconsistency), which silently installs packages where python3 can't see
# them. Explicit -m pip avoids that regardless of whether this venv has it.
"$VENV_DIR/bin/python3" -m pip install --upgrade pip
"$VENV_DIR/bin/python3" -m pip install -r "$SCRIPT_DIR/requirements.txt"

# --- GL sanity check ---------------------------------------------------
log "Verifying a real GL context can be created"
if command -v glxinfo >/dev/null 2>&1; then
  glxinfo -B 2>/dev/null | head -20 || warn "glxinfo ran but returned nothing useful — check DISPLAY is set and an X server is running."
else
  warn "glxinfo not found even after installing mesa-utils — skipping this check."
fi
"$VENV_DIR/bin/python3" -m renderhost.demo_blend --screenshot /tmp/renderhost-setup-check.png \
  && echo "GL render + screenshot OK (/tmp/renderhost-setup-check.png)" \
  || die "renderhost.demo_blend failed to render — check the GL/SDL error above before going further."
rm -f /tmp/renderhost-setup-check.png

# --- display detection ---------------------------------------------------
log "What RandR/SDL actually detects on this machine right now"
"$VENV_DIR/bin/python3" -m renderhost.displays

# --- DPMS/screensaver helper (§4: "otherwise the show blanks mid-set") ----
log "Writing a DPMS/screensaver-disable helper"
cat > "$SCRIPT_DIR/disable-dpms.sh" <<'EOF'
#!/usr/bin/env bash
# Run once per X session before a show (§4) — otherwise the display(s)
# blank mid-set. Not run automatically by setup-render-host.sh since it
# needs an active X session (DISPLAY set), which a fresh install won't have
# yet. Add to your X session's autostart once the rig is otherwise working.
xset -dpms
xset s off
EOF
chmod +x "$SCRIPT_DIR/disable-dpms.sh"
echo "wrote $SCRIPT_DIR/disable-dpms.sh"

# --- generated config templates (review-only) -----------------------------
if [[ "$GEN_CONFIGS" == 1 ]]; then
  CONFIG_DIR="$SCRIPT_DIR/host-configs"
  mkdir -p "$CONFIG_DIR"
  log "Writing config TEMPLATES for review under $CONFIG_DIR — nothing here is applied"

  # §3: isolated point-to-point link, static IP, no DHCP on this segment.
  cat > "$CONFIG_DIR/01-render-host-static.yaml" <<'EOF'
# ARCHITECTURE.md §3 — TEMPLATE, review before applying.
#
# 1. Replace `eth-show` below with the actual interface name for the show
#    NIC on this machine (`ip link` to list them — do this BEFORE editing,
#    since guessing wrong here can cut your own access to the host).
# 2. Copy to /etc/netplan/ on the render host (root, e.g.
#    /etc/netplan/99-render-host.yaml) — DO NOT overwrite an existing
#    numbered file blindly, check what's already there first (`ls /etc/netplan/`).
# 3. `sudo netplan try` first (auto-reverts if you don't confirm within a
#    timeout) — NOT `netplan apply` directly. This is the tested-rollback
#    the doc's own §3 insists on.
# 4. Keep wifi/another interface's route metric lower so this link never
#    becomes the default route (§3: "Ensure the wifi route has the lower
#    metric so the default route does not go down the show link").
network:
  version: 2
  ethernets:
    eth-show:                # <-- REPLACE with the real interface name
      addresses: [192.168.50.1/24]
      # No gateway/DNS on this segment on purpose — it's control traffic
      # only (§3), isolated point-to-point, no DHCP.
EOF
  echo "wrote $CONFIG_DIR/01-render-host-static.yaml (review before use — see comments inside)"

  # §10: systemd user service so the render process outlives the ssh
  # session and restarts on crash.
  cat > "$CONFIG_DIR/render-host@.service" <<EOF
# ARCHITECTURE.md §10 — TEMPLATE, review before installing.
#
# Install (as the render-host's own login user):
#   mkdir -p ~/.config/systemd/user
#   cp render-host@.service ~/.config/systemd/user/
#   systemctl --user daemon-reload
#   systemctl --user enable --now render-host@rig.service   # "rig" here names a profile file, rig.json
#
# %i expands to whatever follows the @ in the unit name you start
# (render-host@rig.service -> %i = "rig") — this template expects a
# profile at \$HOME/renderhost-profiles/%i.json; adjust to taste.
[Unit]
Description=lightsaber render host (%i)
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
ExecStart=$VENV_DIR/bin/python3 -m renderhost.serve %h/renderhost-profiles/%i.json --fullscreen
Restart=on-failure
RestartSec=2
Environment=DISPLAY=:0

[Install]
WantedBy=default.target
EOF
  echo "wrote $CONFIG_DIR/render-host@.service (review before installing — see comments inside)"
fi

# --- what's still manual --------------------------------------------------
log "Done. What's still manual (deliberately not scripted):"
cat <<'EOF'

1. xrandr layout (§4) — venue-specific, depends on what's actually
   connected. Query first, THEN compose:
     xrandr --query
     # example, 4 outputs left-to-right (adjust names/count to what you saw):
     xrandr --output HDMI-A-1 --auto --primary \
            --output HDMI-A-2 --auto --right-of HDMI-A-1 \
            --output DP-1     --auto --right-of HDMI-A-2 \
            --output DP-2     --auto --right-of DP-1
   Then rerun to confirm: .venv/bin/python3 -m renderhost.displays

2. Static network (§3) — review and apply host-configs/01-render-host-static.yaml
   yourself, with `netplan try` (not `apply`), per the comments in that file.

3. systemd service (§10) — review and install host-configs/render-host@.service
   yourself, per the comments in that file.

4. BIOS "iGPU Multi-Monitor" (§2) — only if you're driving a 5th projector
   off the motherboard iGPU because all 5 RX 580 outputs are already used
   for projectors.

5. disable-dpms.sh — run once per X session (or wire into your session's
   autostart) before a show.

See ARCHITECTURE.md §12 for what's actually been verified so far (a laptop
display + one external monitor) vs. what this real rig will be the first
real test of.
EOF
