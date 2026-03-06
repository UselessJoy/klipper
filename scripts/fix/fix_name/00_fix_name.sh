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
    
    # 2. Обновляем /etc/hosts
    log_info "2. Обновляю /etc/hosts"
    
    # Создаем временный файл для безопасного редактирования
    TEMP_HOSTS=$(mktemp)
    sudo_cmd cp /etc/hosts "$TEMP_HOSTS"
    
    # Удаляем старые записи с hostname из IPv4 и IPv6 строк
    sudo_cmd sed -i -E "/^127\.0\.1\.1/ s/[[:space:]]+orangepi3-lts[[:space:]]*/ /g" "$TEMP_HOSTS"
    sudo_cmd sed -i -E "/^::1/ s/[[:space:]]+orangepi3-lts[[:space:]]*/ /g" "$TEMP_HOSTS"
    
    # Добавляем новый hostname
    # Для IPv4 строки
    if sudo_cmd grep -q "^127\.0\.1\.1" "$TEMP_HOSTS"; then
        # Добавляем к существующей строке 127.0.1.1
        sudo_cmd sed -i "/^127\.0\.1\.1/ s/$/ $NEW_HOSTNAME/" "$TEMP_HOSTS"
    else
        # Создаем новую строку
        echo "127.0.1.1 $NEW_HOSTNAME" | sudo_cmd tee -a "$TEMP_HOSTS" > /dev/null
    fi
    
    # Для IPv6 строки
    if sudo_cmd grep -q "^::1" "$TEMP_HOSTS"; then
        # Добавляем к существующей строке ::1
        sudo_cmd sed -i "/^::1/ s/$/ $NEW_HOSTNAME/" "$TEMP_HOSTS"
    fi
    
    # Убираем лишние пробелы
    sudo_cmd sed -i 's/[[:space:]]\+/ /g' "$TEMP_HOSTS"
    
    # Копируем обратно
    sudo_cmd cp "$TEMP_HOSTS" /etc/hosts
    sudo_cmd rm "$TEMP_HOSTS"
    
    log_info "✓ /etc/hosts обновлен"
    
    # 3. Устанавливаем Avahi
    log_info "3. Устанавливаю Avahi"
    if [ "$OS" = "debian" ]; then
        sudo_cmd apt-get install -y avahi-daemon
    elif [ "$OS" = "redos" ]; then
        sudo_cmd dnf install -y avahi
    else
        exit_unsupported_os
    fi
    log_info "✓ Avahi установлен"
    
    # 4. Запускаем службу
    log_info "4. Запускаю службу Avahi"
    if ! sudo_cmd systemctl enable --now avahi-daemon; then
        log_warn "Не удалось запустить службу Avahi"
    else
        log_info "✓ Служба Avahi запущена"
    fi
    
    # 5. Проверяем результат
    log_info "Проверка:"
    log_info "  hostname: $(hostname)"
    log_info "  /etc/hosts:"
    sudo_cmd cat /etc/hosts | while read line; do
        if echo "$line" | grep -q "$NEW_HOSTNAME"; then
            log_info "    $line"
        fi
    done
    
    exit_with_reboot "Имя системы настроено. Теперь ваш компьютер доступен как: $NEW_HOSTNAME.local"
}

# trap_cleanup 'log_warn "Прерывание работы"'
main "$@"