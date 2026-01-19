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
    log_info "Устанавливаю udev-media-automount..."
    
    require_command git "Git"
    require_internet
    
    # Удаляем старую версию, если существует
    if [ -d "$HOME/udev-media-automount" ]; then
        log_info "Удаляю старую версию udev-media-automount"
        rm -rf "$HOME/udev-media-automount"
    fi
    
    # Клонируем репозиторий
    log_info "Клонирую репозиторий..."
    if ! (cd "$HOME" && git clone https://github.com/UselessJoy/udev-media-automount); then
        exit_error "Не удалось клонировать репозиторий"
    fi
    log_info "Репозиторий успешно скачан"
    cd "$HOME/udev-media-automount"
    
    # Устанавливаем
    log_info "Устанавливаю udev-media-automount..."
    if ! sudo_cmd make install; then
        exit_error "Не удалось установить udev-media-automount"
    fi
    
    # Перезагружаем правила udev
    log_info "Перезагружаю правила udev..."
    sudo_cmd udevadm control --reload-rules
    sudo_cmd udevadm trigger
    
    exit_with_reboot "udev-media-automount успешно установлен"
}

# trap_cleanup 'log_warn "Прерывание работы"'
main "$@"