#!/bin/bash

# Common library for fix scripts
# Коды возврата:
# 0 - успех, продолжать без перезагрузки
# 1 - успех, требуется перезагрузка
# 2 - ошибка: нет интернета
# 3 - ошибка выполнения
# 4 - неподдерживаемая ОС
# 5-127 - другие ошибки

set -e
set -o pipefail

# Проверяем, что библиотека не загружается повторно
if [ -z "${FIX_LIB_LOADED:-}" ]; then
    FIX_LIB_LOADED=1
    
    # Определяем корневую директорию Klipper
    if [ -z "${KLIPPER_SCRIPTS_DIR:-}" ]; then
        # Пытаемся определить путь к common_lib.sh
        if [ -n "${BASH_SOURCE[0]}" ]; then
            KLIPPER_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        else
            KLIPPER_SCRIPTS_DIR="/home/orangepi/klipper/scripts/fix"
        fi
    fi
    
    # Логируем путь для отладки
    # echo "DEBUG: KLIPPER_SCRIPTS_DIR = $KLIPPER_SCRIPTS_DIR" >&2
    
    # --- Логирование ---
    log() {
        local level="${1:-INFO}"
        local message="$2"
        local timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
        local script_name="$(basename "${BASH_SOURCE[1]:-$0}")"
        
        # Форматированный вывод
        case "$level" in
            "ERROR")
                echo -e "[$timestamp] [ERROR] [$script_name] $message" >&2
                ;;
            "WARN")
                echo -e "[$timestamp] [WARN]  [$script_name] $message" >&2
                ;;
            "INFO")
                echo -e "[$timestamp] [INFO]  [$script_name] $message"
                ;;
            "DEBUG")
                if [ "${DEBUG_MODE:-0}" = "1" ]; then
                    echo -e "[$timestamp] [DEBUG] [$script_name] $message"
                fi
                ;;
            *)
                echo -e "[$timestamp] [$level] [$script_name] $message"
                ;;
        esac
    }
    
    log_info() {
        log "INFO" "$1"
    }
    
    log_error() {
        log "ERROR" "$1"
    }
    
    log_warn() {
        log "WARN" "$1"
    }
    
    log_debug() {
        log "DEBUG" "$1"
    }
    
    # --- Определение ОС и переменных ---
    detect_os() {
        if command -v apt &> /dev/null; then
            OS="debian"
            PKG_MANAGER="apt-get"
            SUDO_PASS="${SUDO_PASS:-orangepi}"
        elif command -v dnf &> /dev/null; then
            OS="redos"
            PKG_MANAGER="dnf"
            SUDO_PASS="${SUDO_PASS:-user}"
        elif command -v yum &> /dev/null; then
            OS="redhat"
            PKG_MANAGER="yum"
            SUDO_PASS="${SUDO_PASS:-user}"
        elif command -v apk &> /dev/null; then
            OS="alpine"
            PKG_MANAGER="apk"
            SUDO_PASS="${SUDO_PASS:-root}"
        else
            OS="unknown"
            PKG_MANAGER=""
            SUDO_PASS=""
        fi
        
        export OS PKG_MANAGER SUDO_PASS
        log_info "Определена ОС: $OS, менеджер пакетов: $PKG_MANAGER"
    }
    
    # Вызываем автоматическое определение при загрузке
    detect_os
    
    # --- Функции sudo ---
    sudo_cmd() {
        if [ -z "$SUDO_PASS" ]; then
            log_error "Пароль sudo не установлен. Вызовите detect_os или установите SUDO_PASS"
            return 1
        fi
        echo "$SUDO_PASS" | sudo -S --prompt="" -- "$@" > /dev/null 2>&1
    }
    sudo_cmd_with_output() {
        if [ -z "$SUDO_PASS" ]; then
            log_error "Пароль sudo не установлен"
            return 1
        fi
        
        local cmd="$*"
        log_debug "Выполняю sudo команду с выводом: $cmd"
        
        # Используем expect для безопасной передачи пароля
        if command -v expect &> /dev/null; then
            expect << EOF
spawn sudo -S --prompt="" -- $@
expect "password"
send "$SUDO_PASS\r"
expect eof
EOF
            return $?
        else
            # Fallback: использование echo (менее безопасно)
            echo "$SUDO_PASS" | sudo -S --prompt="" -- "$@"
            return $?
        fi
    }
    
    # --- Функции завершения ---
    exit_with_reboot() {
        local message="${1:-Требуется перезагрузка системы}"
        log_info "СКРИПТ УСПЕШЕН: $message"
        exit 1
    }
    
    exit_success() {
        local message="${1:-Скрипт выполнен успешно}"
        log_info "$message"
        exit 0
    }
    
    exit_error() {
        local message="${1:-Произошла ошибка}"
        local code="${2:-3}"
        
        log_error "$message"
        exit $code
    }
    
    exit_unsupported_os() {
        local message="${1:-Неподдерживаемая операционная система}"
        log_error "$message"
        exit 4
    }
    
    # --- Вспомогательные функции ---
    require_internet() {
        log_info "Проверка интернет-соединения..."
        
        # Пробуем разные методы проверки
        local hosts=("8.8.8.8" "1.1.1.1" "google.com")
        local timeout=3
        
        for host in "${hosts[@]}"; do
            if ping -c 1 -W $timeout "$host" &> /dev/null; then
                log_info "Интернет-соединение доступно"
                return 0
            fi
        done
        
        log_error "Нет интернет-соединения"
        return 1
    }
    
    require_command() {
        local cmd="$1"
        local friendly_name="${2:-$cmd}"
        
        if ! command -v "$cmd" &> /dev/null; then
            log_error "Требуется команда: $friendly_name"
            return 1
        fi
        return 0
    }
    
    backup_file() {
        local file="$1"
        local backup_suffix="${2:-.bak}"
        
        if [ -f "$file" ]; then
            local backup="${file}${backup_suffix}"
            if sudo_cmd cp "$file" "$backup"; then
                log_info "Создана резервная копия: $backup"
                return 0
            else
                log_error "Не удалось создать резервную копию: $file"
                return 1
            fi
        else
            log_warn "Файл для резервного копирования не найден: $file"
            return 2
        fi
    }
    
    # --- Обработка сигналов (для чистового завершения) ---
    trap_cleanup() {
        local handler="$1"
        trap "$handler" INT TERM EXIT
        log_debug "Установлен обработчик завершения"
    }
    
    # --- Функция для загрузки библиотеки из других скриптов ---
    load_common_lib() {
        local script_dir="$1"
        local common_lib_path="$KLIPPER_SCRIPTS_DIR/common_lib.sh"
        
        if [ ! -f "$common_lib_path" ]; then
            # Попробуем найти относительно текущего скрипта
            common_lib_path="$(dirname "$script_dir")/common_lib.sh"
        fi
        
        if [ -f "$common_lib_path" ]; then
            source "$common_lib_path"
            return 0
        else
            echo "ОШИБКА: Не найден common_lib.sh" >&2
            return 1
        fi
    }
    
    # --- Проверка зависимостей библиотеки ---
    validate_environment() {
        if [ "$OS" = "unknown" ]; then
            log_warn "Не удалось определить операционную систему"
        fi
        
        if [ -z "$SUDO_PASS" ]; then
            log_warn "Пароль sudo не установлен. Некоторые операции могут не работать"
        fi
        
        return 0
    }
    
    # Автоматическая валидация при загрузке
    validate_environment

fi