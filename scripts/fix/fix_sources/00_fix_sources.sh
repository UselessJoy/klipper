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
        
        # Вариант 1: Использовать временный файл (самый надежный)
        cat > /tmp/sources.list.tmp << 'EOF'
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
        
        # Копируем с использованием sudo_cmd
        if ! sudo_cmd cp /tmp/sources.list.tmp /etc/apt/sources.list; then
            exit_error "Не удалось скопировать sources.list"
        fi
        
        # Проверяем результат
        log_info "Проверяем созданный файл..."
        if [ -f "/etc/apt/sources.list" ] && [ -s "/etc/apt/sources.list" ]; then
            log_info "Файл успешно создан. Размер: $(sudo wc -l < /etc/apt/sources.list) строк"
        else
            exit_error "Файл sources.list пуст или не существует"
        fi
        
        # Удаляем временный файл
        rm -f /tmp/sources.list.tmp
        
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

main "$@"