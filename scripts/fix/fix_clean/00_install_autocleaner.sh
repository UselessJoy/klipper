#!/bin/bash

# Загружаем общую библиотеку
KLIPPER_FIX_DIR="$HOME/klipper/scripts/fix"
FIX="$KLIPPER_FIX_DIR/fix.sh"

if [ ! -f "$FIX" ]; then
    echo "ОШИБКА: Не найден fix.sh" >&2
    exit 3
fi

source "$FIX"

main() {
    log_info "Устанавливаю сервис autocleaner..."
    
    SRCDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    
    # Создаем файл сервиса
    SERVICE_CONTENT=$(cat << EOF
[Unit]
Description=System Cleanup Service
After=network.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$SRCDIR/clear_memory.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    )
    
    log_info "Создаю файл сервиса: /etc/systemd/system/autocleaner.service"
    if ! sudo_cmd sh -c "cat > /etc/systemd/system/autocleaner.service" <<< "$SERVICE_CONTENT"; then
        exit_error "Не удалось создать файл сервиса"
    fi
    
    log_info "Включаю сервис autocleaner"
    if ! sudo_cmd systemctl enable autocleaner.service; then
        log_warn "Не удалось включить сервис autocleaner"
    else
        log_info "✓ Сервис autocleaner включен"
    fi
    
    exit_success "Сервис autocleaner установлен"
}

# trap_cleanup 'log_warn "Прерывание работы"'
main "$@"