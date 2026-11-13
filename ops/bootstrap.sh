#!/usr/bin/env bash
# Turns a fresh Ubuntu server into one that is ready for Mieru deploys.
#
#   First run, as root:  ssh root@<ip> 'bash -s' < ops/bootstrap.sh
#   Any later run:       ssh deploy@<ip> 'sudo bash -s' < ops/bootstrap.sh
#
# Safe to run again: each step checks the current state or rewrites the same file.
# A later run upgrades packages too. When a new Docker Engine is out, its package
# restarts dockerd, and with it every container: run it when a short outage is fine.
# Everything is inside main(), called on the last line, so bash has read the whole
# script before any command runs. A command that reads stdin can't swallow the rest.
set -euo pipefail

readonly DEPLOY_USER=deploy
readonly APP_DIR=/srv/mieru
readonly REPO_URL=https://github.com/AntonBurduzha/mieru.git

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'bootstrap: %s\n' "$*" >&2; exit 1; }

# Wait up to 5 minutes for the dpkg lock instead of failing at once: on a fresh
# server cloud-init or apt-daily often holds it for the first few minutes
apt_get() { apt-get -q -y -o DPkg::Lock::Timeout=300 "$@"; }

check_system() {
  [[ $EUID -eq 0 ]] || die "run as root (or with sudo)"
  local id
  # shellcheck source=/dev/null
  id=$(. /etc/os-release && echo "$ID")
  [[ $id == ubuntu ]] || die "expected Ubuntu, found $id"
}

upgrade_packages() {
  log "Upgrading packages"
  apt_get update
  # Keep the provider's config files when a package ships a new version of one
  apt_get upgrade -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold
  apt_get install ca-certificates curl git unattended-upgrades
}

enable_security_updates() {
  log "Turning on automatic security updates"
  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
  systemctl enable --now unattended-upgrades
}

install_docker() {
  log "Installing Docker Engine and the Compose plugin"
  local codename
  # shellcheck source=/dev/null
  codename=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL --connect-timeout 10 --max-time 60 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt_get update
  apt_get install docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now containerd docker
}

set_timezone() {
  log "Setting the timezone to UTC"
  timedatectl set-timezone UTC
}

create_deploy_user() {
  log "Creating the $DEPLOY_USER user"
  if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    # No password at all: the only way in is an SSH key
    useradd --create-home --shell /bin/bash "$DEPLOY_USER"
  fi
  usermod -aG docker "$DEPLOY_USER"

  # Validate before installing: a broken sudoers file can lock everyone out of sudo
  local rule
  rule=$(mktemp)
  printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$DEPLOY_USER" >"$rule"
  visudo -cf "$rule" || { rm -f "$rule"; die "visudo rejected the sudoers rule"; }
  install -m 0440 "$rule" "/etc/sudoers.d/90-$DEPLOY_USER"
  rm -f "$rule"

  local home
  home=$(getent passwd "$DEPLOY_USER" | cut -d: -f6)
  install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$home/.ssh"
  if [[ ! -s $home/.ssh/authorized_keys ]]; then
    [[ -s /root/.ssh/authorized_keys ]] || die "no key in /root/.ssh/authorized_keys to give $DEPLOY_USER"
    install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /root/.ssh/authorized_keys "$home/.ssh/authorized_keys"
  fi
}

harden_ssh() {
  log "SSH: keys only, no root login"
  # sshd keeps the first value it reads for each option, and reads the drop-ins
  # in name order, so 00- wins over the provider's 50-cloud-init.conf
  local dropin=/etc/ssh/sshd_config.d/00-mieru.conf
  cat >"$dropin" <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
  # A config that fails the check must not stay behind for the next sshd restart
  sshd -t || { rm -f "$dropin"; die "sshd -t failed; $dropin removed"; }
  # Open sessions survive a reload, including the one running this script
  systemctl reload-or-restart ssh
}

clone_repo() {
  log "Cloning the repository into $APP_DIR"
  install -d -m 0755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR"
  if [[ ! -d $APP_DIR/.git ]]; then
    runuser -u "$DEPLOY_USER" -- git clone --quiet "$REPO_URL" "$APP_DIR"
  fi
}

main() {
  export DEBIAN_FRONTEND=noninteractive
  # needrestart: only list the services that need a restart. Packages that restart
  # their own service on upgrade (docker-ce restarts dockerd) still do
  export NEEDRESTART_MODE=l

  check_system
  upgrade_packages
  enable_security_updates
  install_docker
  set_timezone
  create_deploy_user
  harden_ssh
  clone_repo

  log "Done. Check from a second terminal before closing this one: ssh $DEPLOY_USER@<ip> sudo true"
  if [[ -f /var/run/reboot-required ]]; then
    log "A reboot is needed for the upgrades: ssh $DEPLOY_USER@<ip> sudo reboot"
  fi
}

main "$@"
