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
    log_info "Устанавливаю assistant..."
    
    # Определяем путь к пакетам
    PACKAGES_DIR="$KLIPPER_FIX_DIR/fix_assistant"
    
    if [ "$OS" = "debian" ]; then
        PACKAGE_PATH="$PACKAGES_DIR/cassistant_6.5-1_arm64.deb"
        if [ ! -f "$PACKAGE_PATH" ]; then
            exit_error "Не найден deb-пакет: $PACKAGE_PATH"
        fi
        
        log_info "Устанавливаю deb-пакет..."
        if ! sudo_cmd dpkg -i "$PACKAGE_PATH"; then
            log_warn "Установка deb-пакета завершилась с ошибкой, пробую исправить зависимости..."
            sudo_cmd apt-get install -f -y
        fi
        
    elif [ "$OS" = "redos" ]; then
        PACKAGE_PATH="$PACKAGES_DIR/cassistant-6.5-1.aarch64.rpm"
        if [ ! -f "$PACKAGE_PATH" ]; then
            exit_error "Не найден rpm-пакет: $PACKAGE_PATH"
        fi
        
        log_info "Устанавливаю rpm-пакет..."
        if ! sudo_cmd rpm -i "$PACKAGE_PATH"; then
            exit_error "Не удалось установить rpm-пакет"
        fi
        
    else
        exit_unsupported_os
    fi
    
    log_info "Останавливаю и отключаю службу assistant..."
    sudo_cmd systemctl stop assistant.service 2>/dev/null || true
    sudo_cmd systemctl disable assistant.service 2>/dev/null || true
    
    exit_success "Assistant установлен"
}

trap_cleanup 'log_warn "Прерывание работы"'
main "$@"