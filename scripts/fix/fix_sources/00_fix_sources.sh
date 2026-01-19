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
    log_info "Обновление sources.list"
    
    if [ "$OS" = "debian" ]; then
        log_info "Создаю backup файла sources.list..."
        backup_file "/etc/apt/sources.list"
        
        log_info "Записываю новый sources.list..."
        if ! sudo_cmd tee /etc/apt/sources.list > /dev/null << 'EOF'
# Основные репозитории Debian bullseye
deb http://mirrors.tuna.tsinghua.edu.cn/debian bullseye main contrib non-free
deb-src http://mirrors.tuna.tsinghua.edu.cn/debian bullseye main contrib non-free

# Обновления
deb http://mirrors.tuna.tsinghua.edu.cn/debian bullseye-updates main contrib non-free
deb-src http://mirrors.tuna.tsinghua.edu.cn/debian bullseye-updates main contrib non-free

# Безопасность
deb http://mirrors.tuna.tsinghua.edu.cn/debian-security bullseye-security main contrib non-free
deb-src http://mirrors.tuna.tsinghua.edu.cn/debian-security bullseye-security main contrib non-free
EOF
        then
            exit_error "Не удалось записать sources.list"
        fi
        
        log_info "Обновляю список пакетов..."
        if ! sudo_cmd apt-get update --allow-releaseinfo-change; then
            log_warn "apt-get update завершился с предупреждением"
        fi
        
        exit_success "sources.list успешно обновлен"
        
    elif [ "$OS" = "redos" ]; then
        exit_success "Для RedOS обновление не требуется"
    else
        exit_unsupported_os
    fi
}

# trap_cleanup 'log_warn "Прерывание работы"'
main "$@"