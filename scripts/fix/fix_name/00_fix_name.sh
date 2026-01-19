#!/bin/bash

# Загружаем общую библиотеку
KLIPPER_FIX_DIR="$HOME/klipper/scripts/fix"
FIX="$KLIPPER_FIX_DIR/fix.sh"

if [ ! -f "$FIX" ]; then
    echo "ОШИБКА: Не найден fix.sh" >&2
    exit 3
fi

source "$FIX"

# Устанавливаем имя хоста
NEW_HOSTNAME="gelios"

main() {
    log_info "Начинаю настройку имени системы..."
    
    # 1. Меняем hostname
    log_info "1. Устанавливаю hostname: $NEW_HOSTNAME"
    if ! sudo_cmd hostnamectl set-hostname "$NEW_HOSTNAME"; then
        exit_error "Не удалось установить hostname"
    fi
    log_info "✓ Hostname установлен"
    
    # Удаляем старые записи этого хоста (если есть)
    sudo_cmd sed -i "/[[:space:]]$NEW_HOSTNAME[[:space:]]*$/d" /etc/hosts
    sudo_cmd sed -i "/[[:space:]]$NEW_HOSTNAME\.local[[:space:]]*$/d" /etc/hosts
    
    # Добавляем к существующей строке localhost
    if sudo_cmd grep -q "^127\.0\.0\.1[[:space:]]" /etc/hosts; then
        # Если строка с 127.0.0.1 существует, добавляем к ней
        sudo_cmd sed -i "/^127\.0\.0\.1[[:space:]]/ s/$/ $NEW_HOSTNAME/" /etc/hosts
    else
        # Если строки нет, создаем новую
        echo "127.0.0.1 localhost $NEW_HOSTNAME" | sudo_cmd tee -a /etc/hosts > /dev/null
    fi
    
    # 2. Устанавливаем Avahi
    log_info "2. Устанавливаю Avahi"
    if [ "$OS" = "debian" ]; then
        sudo_cmd apt-get install -y avahi-daemon
    elif [ "$OS" = "redos" ]; then
        sudo_cmd dnf install -y avahi
    else
        exit_unsupported_os
    fi
    log_info "✓ Avahi установлен"
    
    # 3. Запускаем службу
    log_info "3. Запускаю службу Avahi"
    if ! sudo_cmd systemctl enable --now avahi-daemon; then
        log_warn "Не удалось запустить службу Avahi"
    else
        log_info "✓ Служба Avahi запущена"
    fi
    
    exit_with_reboot "Имя системы настроено. Теперь ваш компьютер доступен как: $NEW_HOSTNAME.local"
}

# trap_cleanup 'log_warn "Прерывание работы"'
main "$@"