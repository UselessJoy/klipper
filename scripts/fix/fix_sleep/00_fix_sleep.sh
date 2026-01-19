#!/bin/bash

# Загружаем общую библиотеку
KLIPPER_FIX_DIR="$HOME/klipper/scripts/fix"
FIX="$KLIPPER_FIX_DIR/fix.sh"

if [ ! -f "$FIX" ]; then
    echo "ОШИБКА: Не найден fix.sh" >&2
    exit 3
fi

source "$FIX"

# Пути к конфигурационным файлам
LOGIND_CONF="/etc/systemd/logind.conf"
SLEEP_CONF="/etc/systemd/sleep.conf"

# Настройка logind.conf
configure_logind() {
    backup_file "$LOGIND_CONF"
    
    log_info "Настройка logind.conf..."
    
    # Параметры для отключения сна
    declare -A settings=(
        ["HandleSuspendKey"]="ignore"
        ["HandleHibernateKey"]="ignore"
        ["HandleLidSwitch"]="ignore"
        ["HandleLidSwitchExternalPower"]="ignore"
        ["HandleLidSwitchDocked"]="ignore"
        ["IdleAction"]="ignore"
    )
    
    for key in "${!settings[@]}"; do
        sudo_cmd sh -c "
            if grep -q '^$key=' '$LOGIND_CONF'; then
                sed -i 's/^$key=.*/$key=${settings[$key]}/' '$LOGIND_CONF'
            else
                echo '$key=${settings[$key]}' >> '$LOGIND_CONF'
            fi
        "
        log_info "Установлен параметр $key=${settings[$key]}"
    done
    
    # Специфичные настройки для РЕД ОС
    if [ "$OS" = "redos" ]; then
        log_info "Применяю специфичные настройки для RED OS..."
        sudo_cmd sed -i 's/#KillUserProcesses=.*/KillUserProcesses=no/' "$LOGIND_CONF"
        sudo_cmd sed -i 's/#UserTasksMax=.*/UserTasksMax=infinity/' "$LOGIND_CONF"
    fi
}

# Настройка sleep.conf
configure_sleep() {
    backup_file "$SLEEP_CONF"
    
    log_info "Настройка sleep.conf..."
    
    # Содержимое секции Sleep
    SLEEP_CONTENT=$(cat << 'EOF'
[Sleep]
AllowSuspend=no
AllowHibernation=no
AllowHybridSleep=no
AllowSuspendThenHibernate=no
EOF
    )
    
    # Безопасная замена секции
    sudo_cmd sh -c "
        # Удаляем существующую секцию
        sed -i '/^\[Sleep\]/,/^\[/{/^\[Sleep\]/!{/^\[/!d}}' '$SLEEP_CONF'
        sed -i '/^\[Sleep\]/d' '$SLEEP_CONF'
        
        # Добавляем новую секцию
        echo '$SLEEP_CONTENT' >> '$SLEEP_CONF'
    "
    log_info "Настроена секция [Sleep]"
}

# Маскировка systemd targets
mask_sleep_targets() {
    local targets=(
        "sleep.target"
        "suspend.target"
        "hibernate.target"
        "hybrid-sleep.target"
        "suspend-then-hibernate.target"
    )
    
    log_info "Маскирую системные цели..."
    
    for target in "${targets[@]}"; do
        if ! sudo_cmd systemctl is-enabled "$target" | grep -q masked; then
            sudo_cmd systemctl mask "$target"
            log_info "Замаскирована цель: $target"
        else
            log_info "Цель $target уже замаскирована"
        fi
    done
}

# Маскировка systemd services
mask_sleep_services() {
    local services=(
        "systemd-suspend.service"
        "systemd-hibernate.service"
        "systemd-hybrid-sleep.service"
        "systemd-suspend-then-hibernate.service"
    )
    
    log_info "Маскирую системные сервисы..."
    
    for service in "${services[@]}"; do
        if ! sudo_cmd systemctl is-enabled "$service" | grep -q masked; then
            sudo_cmd systemctl mask "$service"
            log_info "Замаскирован сервис: $service"
        else
            log_info "Сервис $service уже замаскирован"
        fi
    done
}

# Применение изменений
apply_changes() {
    log_info "Применяю изменения..."
    sudo_cmd systemctl daemon-reload
    sudo_cmd systemctl restart systemd-logind
    log_info "Изменения применены"
}

# Главная функция
main() {
    log_info "Отключаю режимы сна для ОС: $OS"
    
    configure_logind
    configure_sleep
    mask_sleep_targets
    mask_sleep_services
    apply_changes
    
    exit_success "Режимы сна успешно отключены"
}

# trap_cleanup 'log_warn "Прерывание работы"'
main "$@"