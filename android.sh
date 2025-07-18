#!/bin/bash

# Script para escanear red y redirigir puertos usando socat en Termux
# Autor: Script automatizado para Termux

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Puertos locales disponibles
PUERTOS_LOCALES=(8451 8453 8455 8457 8459)

# Función para mostrar banner
mostrar_banner() {
    echo -e "${BLUE}"
    echo "=================================="
    echo "   ESCANER DE RED Y REDIRECTOR"
    echo "          TERMUX SCRIPT"
    echo "=================================="
    echo -e "${NC}"
}

# Función para validar formato IP
validar_ip() {
    local ip=$1
    if [[ $ip =~ ^192\.168\.0\.[0-9]+$ ]]; then
        local ultimo_octeto=$(echo $ip | cut -d'.' -f4)
        if [[ $ultimo_octeto -ge 0 && $ultimo_octeto -le 255 ]]; then
            return 0
        fi
    fi
    return 1
}

# Función para escanear la red
escanear_red() {
    echo -e "${YELLOW}[INFO]${NC} Iniciando escaneo de red 192.168.0.0-120 en puerto 554..."
    echo -e "${YELLOW}[INFO]${NC} Esto puede tomar unos minutos..."
    
    # Crear archivo temporal para resultados
    local temp_file=$(mktemp)
    
    # Escanear red con nmap
    nmap -p 554 --open -T4 192.168.0.0-120 2>/dev/null | grep -E "Nmap scan report|554/tcp" | while read line; do
        if [[ $line =~ "Nmap scan report for" ]]; then
            current_ip=$(echo $line | grep -oE '192\.168\.0\.[0-9]+')
            echo "$current_ip" >> "$temp_file.tmp"
        elif [[ $line =~ "554/tcp" && $line =~ "open" ]]; then
            # IP tiene puerto 554 abierto
            if [[ -f "$temp_file.tmp" ]]; then
                tail -n1 "$temp_file.tmp" >> "$temp_file"
            fi
        fi
    done
    
    # Leer IPs válidas
    local ips_encontradas=()
    if [[ -f "$temp_file" ]]; then
        while IFS= read -r ip; do
            if validar_ip "$ip"; then
                ips_encontradas+=("$ip")
            fi
        done < "$temp_file"
    fi
    
    # Limpiar archivos temporales
    rm -f "$temp_file" "$temp_file.tmp"
    
    # Mostrar resultados
    if [[ ${#ips_encontradas[@]} -eq 0 ]]; then
        echo -e "${RED}[ERROR]${NC} No se encontraron IPs con puerto 554 abierto"
        return 1
    fi
    
    echo -e "${GREEN}[EXITO]${NC} Se encontraron ${#ips_encontradas[@]} IP(s) con puerto 554 abierto:"
    for ip in "${ips_encontradas[@]}"; do
        echo -e "  ${GREEN}→${NC} $ip:554"
    done
    
    # Configurar redirecciones
    configurar_redirecciones "${ips_encontradas[@]}"
}

# Función para configurar redirecciones
configurar_redirecciones() {
    local ips=("$@")
    local max_redirecciones=${#PUERTOS_LOCALES[@]}
    local redirecciones_creadas=0
    
    echo -e "\n${YELLOW}[INFO]${NC} Configurando redirecciones de puertos..."
    
    for i in "${!ips[@]}"; do
        if [[ $redirecciones_creadas -ge $max_redirecciones ]]; then
            echo -e "${YELLOW}[ADVERTENCIA]${NC} Se alcanzó el límite de puertos locales disponibles ($max_redirecciones)"
            break
        fi
        
        local ip="${ips[$i]}"
        local puerto_local="${PUERTOS_LOCALES[$redirecciones_creadas]}"
        
        # Crear redirección con socat
        echo -e "${BLUE}[INFO]${NC} Creando redirección: localhost:$puerto_local → $ip:554"
        
        # Ejecutar socat en background
        socat TCP-LISTEN:$puerto_local,fork TCP:$ip:554 &
        local pid=$!
        
        if [[ $? -eq 0 ]]; then
            echo -e "${GREEN}[EXITO]${NC} Redirección activa - PID: $pid"
            echo "$pid" >> /tmp/socat_pids.txt
            redirecciones_creadas=$((redirecciones_creadas + 1))
        else
            echo -e "${RED}[ERROR]${NC} Error al crear redirección para $ip:554"
        fi
    done
    
    if [[ $redirecciones_creadas -gt 0 ]]; then
        echo -e "\n${GREEN}[RESUMEN]${NC} Se crearon $redirecciones_creadas redirecciones activas"
        echo -e "${YELLOW}[INFO]${NC} Para detener las redirecciones, usa la opción 2 del menú"
    fi
}

# Función para matar todos los procesos socat
matar_socat() {
    echo -e "${YELLOW}[INFO]${NC} Deteniendo todas las redirecciones activas..."
    
    # Buscar PIDs guardados
    if [[ -f /tmp/socat_pids.txt ]]; then
        local pids_detenidos=0
        while IFS= read -r pid; do
            if kill "$pid" 2>/dev/null; then
                echo -e "${GREEN}[EXITO]${NC} Proceso detenido - PID: $pid"
                pids_detenidos=$((pids_detenidos + 1))
            fi
        done < /tmp/socat_pids.txt
        
        rm -f /tmp/socat_pids.txt
        echo -e "${GREEN}[RESUMEN]${NC} Se detuvieron $pids_detenidos procesos"
    fi
    
    # Buscar y matar cualquier proceso socat restante
    local socat_pids=$(pgrep socat)
    if [[ -n "$socat_pids" ]]; then
        echo -e "${YELLOW}[INFO]${NC} Deteniendo procesos socat adicionales..."
        pkill socat
        echo -e "${GREEN}[EXITO]${NC} Todos los procesos socat han sido detenidos"
    else
        echo -e "${BLUE}[INFO]${NC} No se encontraron procesos socat activos"
    fi
}

# Función para mostrar procesos activos
mostrar_procesos() {
    echo -e "${YELLOW}[INFO]${NC} Procesos socat activos:"
    local procesos=$(pgrep -l socat)
    
    if [[ -n "$procesos" ]]; then
        echo "$procesos" | while read pid name; do
            echo -e "${GREEN}→${NC} PID: $pid - $name"
        done
    else
        echo -e "${BLUE}[INFO]${NC} No hay procesos socat activos"
    fi
}

# Función para verificar dependencias
verificar_dependencias() {
    local dependencias=("nmap" "socat")
    local faltantes=()
    
    for dep in "${dependencias[@]}"; do
        if ! command -v "$dep" &> /dev/null; then
            faltantes+=("$dep")
        fi
    done
    
    if [[ ${#faltantes[@]} -gt 0 ]]; then
        echo -e "${RED}[ERROR]${NC} Faltan las siguientes dependencias:"
        for dep in "${faltantes[@]}"; do
            echo -e "  ${RED}→${NC} $dep"
        done
        echo -e "\n${YELLOW}[SOLUCION]${NC} Instala las dependencias con:"
        echo -e "  ${BLUE}pkg install nmap socat${NC}"
        exit 1
    fi
}

# Función para mostrar menú
mostrar_menu() {
    echo -e "\n${BLUE}===== MENU PRINCIPAL =====${NC}"
    echo "1. Escanear red y configurar redirecciones"
    echo "2. Detener todas las redirecciones"
    echo "3. Mostrar procesos activos"
    echo "4. Salir"
    echo -e "${BLUE}===========================${NC}"
    echo -n "Selecciona una opción: "
}

# Función principal
main() {
    mostrar_banner
    verificar_dependencias
    
    while true; do
        mostrar_menu
        read -r opcion
        
        case $opcion in
            1)
                echo -e "\n${YELLOW}[INFO]${NC} Iniciando escaneo de red..."
                escanear_red
                ;;
            2)
                echo -e "\n${YELLOW}[INFO]${NC} Deteniendo redirecciones..."
                matar_socat
                ;;
            3)
                echo -e "\n${YELLOW}[INFO]${NC} Consultando procesos activos..."
                mostrar_procesos
                ;;
            4)
                echo -e "\n${GREEN}[INFO]${NC} Saliendo del script..."
                exit 0
                ;;
            *)
                echo -e "\n${RED}[ERROR]${NC} Opción no válida. Intenta de nuevo."
                ;;
        esac
        
        echo -e "\n${YELLOW}Presiona Enter para continuar...${NC}"
        read -r
    done
}

# Ejecutar función principal
main "$@"