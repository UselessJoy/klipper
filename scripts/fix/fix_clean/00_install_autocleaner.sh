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
    
    # Создаем временный файл с содержимым сервиса
    TEMP_SERVICE=$(mktemp)
    cat > "$TEMP_SERVICE" << EOF
[Unit]
Description=System Cleanup Service
After=multi-user.target
Wants=multi-user.target

[Service]
Type=oneshot
ExecStart=$SRCDIR/clear_memory.sh
RemainAfterExit=no
WorkingDirectory=$HOME
Nice=19
IOSchedulingClass=idle
IOSchedulingPriority=7
KillMode=process

[Install]
WantedBy=multi-user.target
EOF
    
    log_info "Создаю файл сервиса: /etc/systemd/system/autocleaner.service"
    
    # Копируем временный файл в целевую директорию с правами суперпользователя
    if ! sudo_cmd cp "$TEMP_SERVICE" /etc/systemd/system/autocleaner.service; then
        rm -f "$TEMP_SERVICE"
        exit_error "Не удалось создать файл сервиса"
    fi
    
    # Удаляем временный файл
    rm -f "$TEMP_SERVICE"
    
    # Устанавливаем правильные права доступа
    sudo_cmd chmod 644 /etc/systemd/system/autocleaner.service
    
    # Перезагружаем конфигурацию systemd
    sudo_cmd systemctl daemon-reload
    
    log_info "Включаю сервис autocleaner"
    if ! sudo_cmd systemctl enable autocleaner.service; then
        log_warn "Не удалось включить сервис autocleaner"
    else
        log_info "✓ Сервис autocleaner включен"
    fi
    
    # Не запускаем сразу - пусть работает через таймер или при следующей загрузке
    log_info "Сервис будет запущен при следующей загрузке"
    
    exit_success "Сервис autocleaner установлен"
}

main "$@"