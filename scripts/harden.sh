#!/usr/bin/env bash
# Приводит свежий Ubuntu-сервер в безопасное состояние. Идемпотентен.
# ВАЖНО: запускать только когда вход по SSH-ключу уже работает — скрипт выключает
# вход по паролю, и без ключа останется только консоль хостера.
set -euo pipefail

echo "→ проверяю, что ключи на месте"
test -s /root/.ssh/authorized_keys || { echo "authorized_keys пуст — прерываю"; exit 1; }

echo "→ обновления безопасности"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq unattended-upgrades fail2ban >/dev/null
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

echo "→ SSH: только ключи, без root-пароля"
# sshd берёт ПЕРВОЕ вхождение директивы, а Include стоит в начале конфига. Файл
# 50-cloud-init.conf включает пароли, поэтому наш должен читаться раньше — отсюда 00-.
rm -f /etc/ssh/sshd_config.d/99-hardening.conf
if [ -f /etc/ssh/sshd_config.d/50-cloud-init.conf ]; then
  sed -i 's/^\s*PasswordAuthentication\b/#&/I' /etc/ssh/sshd_config.d/50-cloud-init.conf
fi
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
ClientAliveInterval 300
ClientAliveCountMax 2
EOF
sshd -t
systemctl reload ssh || systemctl reload sshd

echo "→ fail2ban для SSH"
cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
backend = systemd
maxretry = 4
findtime = 10m
bantime = 1h
EOF
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

echo "→ файрвол"
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw default deny incoming >/dev/null
ufw --force enable >/dev/null

echo "→ права на секреты"
chmod 600 /opt/neyro/.env 2>/dev/null || true
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys

echo
echo "=== ИТОГ ==="
sshd -T | grep -E '^(passwordauthentication|permitrootlogin|permitemptypasswords|maxauthtries) '
ufw status | head -6
systemctl is-active fail2ban | sed 's/^/fail2ban: /'
ss -tlnp | awk 'NR==1 || /0\.0\.0\.0|\[::\]/' | grep -v 127.0.0
