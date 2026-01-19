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
    log_info "Копирую gcodes..."
    
    SOURCE_DIR="$HOME/printer_data/gcodes"
    TARGET_DIR="$HOME/printer_data/mmcblk0p1/gcodes"
    
    if [ ! -d "$SOURCE_DIR" ]; then
        log_info "Директория $SOURCE_DIR не существует"
        exit_success "Нет gcodes для копирования"
    fi

    # Подсчитываем файлы (не директории)
    file_count=$(find "$SOURCE_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)

    if [ "$file_count" -eq 0 ]; then
        exit_success "Нет файлов для копирования в $SOURCE_DIR"
    fi

    # Копируем файлы
    log_info "Копирую файлы из $SOURCE_DIR в $TARGET_DIR"
    if ! cp -r "$SOURCE_DIR"/* "$TARGET_DIR"/; then
        exit_error "Не удалось скопировать файлы"
    fi
    
    # Очищаем исходную директорию
    log_info "Очищаю исходную директорию..."
    if ! rm -rf "$SOURCE_DIR"/*; then
        log_warn "Не удалось полностью очистить исходную директорию"
    fi
    
    exit_success "Gcodes успешно скопированы"
}

# trap_cleanup 'log_warn "Прерывание работы"'
main "$@"