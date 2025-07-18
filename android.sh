#!/data/data/com.termux/files/usr/bin/bash

# ======= CONFIGURACIÓN =======
# Formato: "MAC|PUERTO_LOCAL|PUERTO_CAMARA"
CÁMARAS=(
  
)

INTERVALO=30  # Intervalo de escaneo (segundos)
RED="192.168.0"  # Prefijo de red local
# ==============================

# Función para escanear la red y poblar la tabla ARP
function escanear_red() {
  echo "[*] Escaneando red $RED.0/24..."
  for i in $(seq 1 254); do
    ping -c 1 -W 1 "$RED.$i" >/dev/null 2>&1 &
  done
  wait
}

# Función para obtener la IP por MAC
function obtener_ip_por_mac() {
  local mac=$1
  arp -a | grep -i "$mac" | awk '{print $2}' | tr -d '()'
}

# Diccionarios en memoria
declare -A IP_ANTERIOR
declare -A PID_SOCAT

# Bucle principal
while true; do
  escanear_red

  for item in "${CÁMARAS[@]}"; do
    IFS="|" read -r mac puerto_local puerto_cam <<< "$item"

    ip_actual=$(obtener_ip_por_mac "$mac")

    if [[ -n "$ip_actual" ]]; then
      if [[ "${IP_ANTERIOR[$mac]}" != "$ip_actual" ]]; then
        echo "[+] Cámara $mac con nueva IP: $ip_actual (puerto local $puerto_local → $ip_actual:$puerto_cam)"

        # Matar redirección anterior si existía
        if [[ -n "${PID_SOCAT[$mac]}" ]]; then
          kill "${PID_SOCAT[$mac]}" 2>/dev/null
          echo "[*] Redirección anterior (PID ${PID_SOCAT[$mac]}) detenida."
        fi

        # Lanzar nueva redirección con socat
        nohup socat TCP-LISTEN:${puerto_local},fork TCP:${ip_actual}:${puerto_cam} >/dev/null 2>&1 &
        PID_SOCAT[$mac]=$!
        IP_ANTERIOR[$mac]=$ip_actual

        echo "[+] Redirección activa en segundo plano (PID ${PID_SOCAT[$mac]})"
      else
        echo "[*] Cámara $mac sin cambios de IP: $ip_actual"
      fi
    else
      echo "[!] Cámara $mac no encontrada en la red"
    fi
  done

  echo
  sleep "$INTERVALO"
done
