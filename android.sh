#!/data/data/com.termux/files/usr/bin/bash

CAM_PORT=554
PUERTOS_LOCALES=(8451 8453 8455 8457 8459)
PID_FILE="/data/data/com.termux/files/usr/tmp/socat_pids.txt"

BLOQUES=(
  "192.168.0.0-63"
  "192.168.0.64-101"
)

function escanear_bloques() {
  local ip
  local ips_encontradas=()

  echo "🔍 Escaneando bloques con nmap..."

  for bloque in "${BLOQUES[@]}"; do
    echo "  Escaneando rango $bloque ..."
    # Mejorado: filtrar líneas vacías y espacios directamente en el pipeline
    mapfile -t ips < <(nmap -p $CAM_PORT --open --max-retries 1 --host-timeout 2s "$bloque" -oG - | awk '/554\/open/{print $2}' | grep -v '^[[:space:]]*$' | tr -d '[:space:]')
    ips_encontradas+=("${ips[@]}")
  done

  printf "%s\n" "${ips_encontradas[@]}"
}

function redirigir_a_camaras() {
  local index=0
  > "$PID_FILE"

  mapfile -t camaras < <(escanear_bloques | sort -u)

  echo "Cámaras encontradas: ${#camaras[@]}"

  for ip in "${camaras[@]}"; do
    # Validación más robusta de IP
    if [[ -z "$ip" || "$ip" =~ ^[[:space:]]*$ ]]; then
        echo "⚠️  IP vacía o con espacios detectada, saltando..."
        continue
    fi
    
    # Validación adicional: verificar que la IP tenga formato válido
    if ! [[ "$ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        echo "⚠️  IP inválida detectada: '$ip', saltando..."
        continue
    fi

    if (( index >= ${#PUERTOS_LOCALES[@]} )); then
      echo "⚠️  Se detectaron más de ${#PUERTOS_LOCALES[@]} cámaras. Solo se redirigen las primeras 5."
      break
    fi

    local puerto_local=${PUERTOS_LOCALES[$index]}
    echo "🔁 Redirigiendo puerto local $puerto_local → $ip:$CAM_PORT"

    nohup socat TCP-LISTEN:$puerto_local,fork TCP:$ip:$CAM_PORT >/dev/null 2>&1 &
    echo $! >> "$PID_FILE"
    echo "✅ Redirección activa para $ip en puerto local $puerto_local (PID $!)"
    ((index++))
  done

  if (( index == 0 )); then
    echo "❌ No se encontraron cámaras con el puerto $CAM_PORT abierto."
  fi
}

function matar_redirecciones() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "ℹ️  No hay procesos socat guardados para matar."
    return
  fi

  echo "🛑 Matando procesos socat anteriores..."
  while read -r pid; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "✔️  Proceso socat (PID $pid) detenido."
    fi
  done < "$PID_FILE"
  rm -f "$PID_FILE"
  echo "✅ Todos los procesos socat finalizados."
}

function menu() {
  while true; do
    echo ""
    echo "====== Redirección automática de cámaras RTSP ======"
    echo "1) Buscar cámaras y redirigir automáticamente (máx 5)"
    echo "2) Matar redirecciones socat activas"
    echo "3) Salir"
    read -rp "Elige una opción: " opt

    case "$opt" in
      1) redirigir_a_camaras ;;
      2) matar_redirecciones ;;
      3) exit 0 ;;
      *) echo "❌ Opción inválida, intenta de nuevo." ;;
    esac
    echo
  done
}

menu