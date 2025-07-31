#!/bin/sh
set -e

if command -v apt &> /dev/null; then
  OS="debian"
  SUDO_PASS="orangepi"
elif command -v dnf &> /dev/null; then
  OS="redos"
  SUDO_PASS="user"
else
  echo "Unknown OS"
  exit 1
fi

# Исправленная функция для выполнения команд с sudo
sudo_cmd() {
    echo "$SUDO_PASS" | sudo -S --prompt="" -- "$@" > /dev/null 2>&1
}

# Пути к конфигурационным файлам
LOGIND_CONF="/etc/systemd/logind.conf"
SLEEP_CONF="/etc/systemd/sleep.conf"

# Резервное копирование оригинальных конфигов
backup_config() {
    local file=$1
    if [ -f "$file" ] && [ ! -f "${file}.bak" ]; then
        sudo_cmd cp "$file" "${file}.bak"
        echo "Created backup: ${file}.bak"
    fi
}

# Настройка logind.conf
configure_logind() {
    backup_config "$LOGIND_CONF"
    
    # Параметры для отключения сна
    declare -A settings=(
        ["HandleSuspendKey"]="ignore"
        ["HandleHibernateKey"]="ignore"
        ["HandleLidSwitch"]="ignore"
        ["HandleLidSwitchExternalPower"]="ignore"
        ["HandleLidSwitchDocked"]="ignore"
        ["IdleAction"]="ignore"
    )
    
    for key in "${!settings[@]}"; do
        # Используем временный файл вместо конвейера
        sudo_cmd sh -c "grep -q '^$key=' $LOGIND_CONF && \
            sed -i 's/^$key=.*/$key=${settings[$key]}/' $LOGIND_CONF || \
            echo '$key=${settings[$key]}' >> $LOGIND_CONF"
        echo "Configured $key=${settings[$key]}"
    done
    
    # Специфичные настройки для РЕД ОС
    if [ "$OS" = "redos" ]; then
        echo "Applying RED OS specific settings..."
        sudo_cmd sed -i 's/#KillUserProcesses=.*/KillUserProcesses=no/' "$LOGIND_CONF"
        sudo_cmd sed -i 's/#UserTasksMax=.*/UserTasksMax=infinity/' "$LOGIND_CONF"
    fi
}

# Настройка sleep.conf
configure_sleep() {
    backup_config "$SLEEP_CONF"
    
    # Используем временный блок для записи
    sudo_cmd sh -c "grep -q '^\[Sleep\]' $SLEEP_CONF || echo '[Sleep]' >> $SLEEP_CONF"
    sudo_cmd sed -i '/^\[Sleep\]/,/^\[/{/^\[Sleep\]/!{/^\[/!d}}' "$SLEEP_CONF"
    
    # Записываем параметры через временную переменную
    {
        echo "AllowSuspend=no"
        echo "AllowHibernation=no"
        echo "AllowHybridSleep=no"
        echo "AllowSuspendThenHibernate=no"
    } | sudo_cmd tee -a "$SLEEP_CONF" > /dev/null
    echo "Configured [Sleep] section"
}

# Маскировка systemd targets
mask_sleep_targets() {
    local targets=(
        "sleep.target"
        "suspend.target"
        "hibernate.target"
        "hybrid-sleep.target"
        "suspend-then-hibernate.target"
    )
    
    for target in "${targets[@]}"; do
        if ! sudo_cmd systemctl is-enabled "$target" | grep -q masked; then
            sudo_cmd systemctl mask "$target"
            echo "Masked $target"
        else
            echo "$target already masked"
        fi
    done
}

# Маскировка systemd services
mask_sleep_services() {
    local services=(
        "systemd-suspend.service"
        "systemd-hibernate.service"
        "systemd-hybrid-sleep.service"
        "systemd-suspend-then-hibernate.service"
    )
    
    for service in "${services[@]}"; do
        if ! sudo_cmd systemctl is-enabled "$service" | grep -q masked; then
            sudo_cmd systemctl mask "$service"
            echo "Masked $service"
        else
            echo "$service already masked"
        fi
    done
}

# Применение изменений
apply_changes() {
    sudo_cmd systemctl daemon-reload
    sudo_cmd systemctl restart systemd-logind
    echo "Changes applied"
}

# Главная функция
main() {
    echo "=== Disabling sleep modes for $OS ==="
    configure_logind
    configure_sleep
    mask_sleep_targets
    mask_sleep_services
    apply_changes
    echo "=== Sleep modes disabled successfully ==="
    exit 0
}

main